"""Space weather clients: NASA DONKI and NOAA SWPC.

Downloads real space weather events and stores them in ``space_weather_events``,
which works as a local cache. The rest of the app (generator, dashboard, alerts)
reads only that table, so it keeps working without internet.

Patterns:
    * **Adapter**: each source (DONKI, NOAA) translates its own JSON format into the
      same ``SpaceWeatherEvent`` object.
    * **Strategy / Protocol**: every client implements ``SpaceWeatherSource``. Adding
      a new source only means writing a new class with ``fetch(start, end)``.
    * **Dependency injection**: clients receive a ``JsonFetcher``. In production it is
      ``HttpJsonFetcher`` (requests + retries). In tests it is a fake with saved
      responses, so tests never touch the network.
    * **Fallback**: DONKI tries several base URLs in order. If every source fails,
      ``sync_space_weather`` reports the error and keeps the existing cache.

Sources:
    * DONKI web services (CCMC): https://ccmc.gsfc.nasa.gov/tools/DONKI
    * NOAA SWPC GOES proton flux: https://www.swpc.noaa.gov/products/goes-proton-flux
    * S1 threshold: >=10 MeV integral flux >= 10 pfu for 3 consecutive 5-min readings
      (NOAA S-scale, Bain et al. 2021).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import obtener_config

logger = logging.getLogger(__name__)

Source = Literal["nasa_donki", "noaa_swpc"]

DONKI_BASE_URLS: tuple[str, ...] = (
    "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get",  # CCMC, no key required
    "https://api.nasa.gov/DONKI",                     # api.nasa.gov, uses NASA_API_KEY
    "https://ccmc.gsfc.nasa.gov/DONKI-API/get",       # New CCMC address (since Sep 2026)
)
NOAA_BASE_URL: str = "https://services.swpc.noaa.gov/json/goes/primary"

PROTON_ENERGY: str = ">=10 MeV"
S1_THRESHOLD_PFU: float = 10.0
S1_MIN_CONSECUTIVE: int = 3
NOAA_S_SCALE_PFU: tuple[tuple[str, float], ...] = (
    ("S5", 1e5), ("S4", 1e4), ("S3", 1e3), ("S2", 1e2), ("S1", 1e1),
)
FLARE_CLASS_W_M2: dict[str, float] = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}


class SpaceWeatherUnavailable(RuntimeError):
    """Raised when a source cannot be reached or returns an invalid response."""


class HostUnreachable(SpaceWeatherUnavailable):
    """The server did not accept the connection (DNS, refused, connect timeout).

    Unlike a slow or failed response for one endpoint, this means the whole host
    is down, so the remaining requests to it can be skipped.
    """


# =====================================================================
# 1. DOMAIN OBJECT
# =====================================================================

@dataclass(frozen=True)
class SpaceWeatherEvent:
    """Space weather event in a source-independent format.

    Attributes:
        external_id: ID of the event in its source (used to avoid duplicates).
        source: "nasa_donki" or "noaa_swpc".
        event_type: "SEP", "FLR", "CME", "GST" or "proton_flux".
        start_time: Event start (UTC).
        peak_time: Peak, if known.
        end_time: End, if known.
        intensity: Flare peak flux (W/m²), peak pfu or max Kp, depending on the type.
        intensity_unit: Unit of ``intensity``.
        raw: Original JSON, kept for traceability.
    """

    external_id: str
    source: Source
    event_type: str
    start_time: datetime
    peak_time: datetime | None = None
    end_time: datetime | None = None
    intensity: float | None = None
    intensity_unit: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def to_row(self) -> dict[str, Any]:
        """Columns for ``ClimaEspacialRepo.insertar_nuevos``."""
        return {
            "external_id": self.external_id,
            "source": self.source,
            "event_type": self.event_type,
            "start_time": self.start_time,
            "peak_time": self.peak_time,
            "end_time": self.end_time,
            "intensity": self.intensity,
            "intensity_unit": self.intensity_unit,
            "raw_json": dict(self.raw) if self.raw else None,
        }


# =====================================================================
# 2. HTTP LAYER (injectable)
# =====================================================================

JsonFetcher = Callable[[str, Mapping[str, Any] | None], Any]
"""``fetcher(url, params) -> parsed JSON``. Raises ``SpaceWeatherUnavailable`` on error."""


class HttpJsonFetcher:
    """GET + JSON with short timeouts.

    Retries only when the server answers with a temporary error (429/5xx). A host
    that does not answer at all fails fast instead of being retried, so a dead
    API never blocks the app for minutes.
    """

    def __init__(
        self, connect_timeout_s: float = 5.0, read_timeout_s: float = 15.0, retries: int = 2
    ) -> None:
        self._timeout_s = (connect_timeout_s, read_timeout_s)
        self._session = requests.Session()
        retry = Retry(
            total=retries,
            connect=0,
            read=0,
            status=retries,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=False,  # A 429 with "retry in 1 h" must not block us
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.headers["Accept"] = "application/json"

    def __call__(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        try:
            response = self._session.get(url, params=params, timeout=self._timeout_s)
            response.raise_for_status()
            return response.json() if response.content.strip() else []
        except requests.ConnectionError as exc:  # Includes ConnectTimeout, not ReadTimeout
            raise HostUnreachable(f"GET {url} unreachable: {exc}") from exc
        except (requests.RequestException, ValueError) as exc:
            raise SpaceWeatherUnavailable(f"GET {url} failed: {exc}") from exc


# =====================================================================
# 3. PARSING HELPERS (pure functions)
# =====================================================================

def parse_time(value: str | None) -> datetime | None:
    """Parses DONKI/NOAA timestamps ("2024-05-10T17:44Z", "...:00Z") to UTC."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def flare_class_to_flux(class_type: str | None) -> float | None:
    """Converts a GOES flare class ("X8.7", "M1.6") to peak flux in W/m²."""
    if not class_type:
        return None
    letter, magnitude = class_type[0].upper(), class_type[1:]
    try:
        return FLARE_CLASS_W_M2[letter] * float(magnitude or 1.0)
    except (KeyError, ValueError):
        return None


def noaa_s_scale(flux_pfu: float) -> str | None:
    """NOAA Solar Radiation Storm level (S1-S5) for a >=10 MeV flux, or None."""
    for level, threshold in NOAA_S_SCALE_PFU:
        if flux_pfu >= threshold:
            return level
    return None


# =====================================================================
# 4. SOURCES (Adapters)
# =====================================================================

class SpaceWeatherSource(Protocol):
    """Common interface for every space weather source.

    A source may also expose:
        * ``partial_errors: dict[str, str]``: parts that failed while the rest worked.
        * ``collected: list[SpaceWeatherEvent]``: events gathered so far, used when
          the source is still running at the sync deadline.
    """

    name: Source

    def fetch(self, start: date, end: date) -> list[SpaceWeatherEvent]:
        """Returns the events between two dates (inclusive)."""
        ...


class DonkiClient:
    """NASA DONKI adapter (SEP, flares, CMEs and geomagnetic storms).

    Tries each base URL in order. The first one that answers is used. Each event
    type is independent: if one fails, the others are still returned and the
    failure is listed in ``partial_errors``. Only if every type fails does
    ``fetch`` raise ``SpaceWeatherUnavailable``.

    Time budget: the whole ``fetch`` stops trying new requests after
    ``time_budget_s`` (60 s by default) and keeps what it already has.

    Circuit breaker: a base URL whose host is unreachable is skipped for the rest
    of the ``fetch``, so a dead host costs one connection attempt, not one per
    event type. A slow or failed response only affects that event type.
    """

    name: Source = "nasa_donki"
    DEFAULT_TYPES: tuple[str, ...] = ("SEP", "FLR", "CME", "GST")

    def __init__(
        self,
        fetcher: JsonFetcher | None = None,
        base_urls: Sequence[str] = DONKI_BASE_URLS,
        api_key: str | None = None,
        event_types: Sequence[str] = DEFAULT_TYPES,
        time_budget_s: float = 60.0,
    ) -> None:
        self._fetcher = fetcher or HttpJsonFetcher()
        self._time_budget_s = time_budget_s
        self._started = 0.0
        self._base_urls = tuple(base_urls)
        self._api_key = api_key or obtener_config().nasa_api_key
        self._event_types = tuple(event_types)
        self.partial_errors: dict[str, str] = {}
        self.collected: list[SpaceWeatherEvent] = []
        self._dead_urls: set[str] = set()
        self._parsers: dict[str, Callable[[Mapping[str, Any]], SpaceWeatherEvent | None]] = {
            "SEP": self._parse_sep,
            "FLR": self._parse_flare,
            "CME": self._parse_cme,
            "GST": self._parse_gst,
        }

    def fetch(self, start: date, end: date) -> list[SpaceWeatherEvent]:
        self.partial_errors = {}
        self._dead_urls = set()
        self._started = time.monotonic()
        self.collected = events = []
        for event_type in self._event_types:
            try:
                items = self._get(event_type, start, end)
            except SpaceWeatherUnavailable as exc:
                self.partial_errors[event_type] = str(exc)
                continue
            for item in items:
                parsed = self._parsers[event_type](item)
                if parsed is not None:
                    events.append(parsed)
        if len(self.partial_errors) == len(self._event_types):
            raise SpaceWeatherUnavailable(" | ".join(self.partial_errors.values()))
        return events

    def _get(self, event_type: str, start: date, end: date) -> list[Mapping[str, Any]]:
        params: dict[str, Any] = {"startDate": start.isoformat(), "endDate": end.isoformat()}
        errors: list[str] = []
        for base in self._base_urls:
            if time.monotonic() - self._started > self._time_budget_s:
                errors.append(f"time budget of {self._time_budget_s:.0f} s exceeded")
                break
            if base in self._dead_urls:
                continue
            query = dict(params)
            if "api.nasa.gov" in base:
                query["api_key"] = self._api_key
            try:
                data = self._fetcher(f"{base}/{event_type}", query)
                return list(data or [])
            except SpaceWeatherUnavailable as exc:
                errors.append(str(exc))
                if isinstance(exc, HostUnreachable):
                    self._dead_urls.add(base)
                logger.warning("DONKI %s unavailable at %s", event_type, base)
        if not errors:
            errors.append("every DONKI address already failed in this run")
        raise SpaceWeatherUnavailable(f"DONKI {event_type}: " + " | ".join(errors))

    # --- One adapter per event type ----------------------------------------
    def _parse_sep(self, item: Mapping[str, Any]) -> SpaceWeatherEvent | None:
        start = parse_time(item.get("eventTime"))
        if not item.get("sepID") or start is None:
            return None
        return SpaceWeatherEvent(
            external_id=item["sepID"], source=self.name, event_type="SEP",
            start_time=start, raw=item,
        )

    def _parse_flare(self, item: Mapping[str, Any]) -> SpaceWeatherEvent | None:
        start = parse_time(item.get("beginTime"))
        if not item.get("flrID") or start is None:
            return None
        return SpaceWeatherEvent(
            external_id=item["flrID"], source=self.name, event_type="FLR",
            start_time=start, peak_time=parse_time(item.get("peakTime")),
            end_time=parse_time(item.get("endTime")),
            intensity=flare_class_to_flux(item.get("classType")), intensity_unit="W/m2",
            raw=item,
        )

    def _parse_cme(self, item: Mapping[str, Any]) -> SpaceWeatherEvent | None:
        start = parse_time(item.get("startTime"))
        if not item.get("activityID") or start is None:
            return None
        speeds = [
            a.get("speed") for a in (item.get("cmeAnalyses") or [])
            if isinstance(a, Mapping) and a.get("speed") is not None
        ]
        return SpaceWeatherEvent(
            external_id=item["activityID"], source=self.name, event_type="CME",
            start_time=start, intensity=max(speeds) if speeds else None,
            intensity_unit="km/s" if speeds else None, raw=item,
        )

    def _parse_gst(self, item: Mapping[str, Any]) -> SpaceWeatherEvent | None:
        start = parse_time(item.get("startTime"))
        if not item.get("gstID") or start is None:
            return None
        kp_values = [
            k.get("kpIndex") for k in (item.get("allKpIndex") or [])
            if isinstance(k, Mapping) and k.get("kpIndex") is not None
        ]
        return SpaceWeatherEvent(
            external_id=item["gstID"], source=self.name, event_type="GST",
            start_time=start, intensity=max(kp_values) if kp_values else None,
            intensity_unit="Kp" if kp_values else None, raw=item,
        )


@dataclass(frozen=True)
class ProtonReading:
    """One GOES 5-minute integral proton flux reading (>=10 MeV)."""

    time: datetime
    flux_pfu: float


def detect_proton_events(
    readings: Iterable[ProtonReading],
    threshold_pfu: float = S1_THRESHOLD_PFU,
    min_consecutive: int = S1_MIN_CONSECUTIVE,
) -> list[SpaceWeatherEvent]:
    """Finds solar proton events: flux >= threshold for N consecutive readings.

    Each event starts at the first reading above threshold, ends at the last one,
    and reports the peak flux. The NOAA S level is stored in ``raw``.
    """
    events: list[SpaceWeatherEvent] = []
    run: list[ProtonReading] = []

    def close_run() -> None:
        if len(run) >= min_consecutive:
            peak = max(run, key=lambda r: r.flux_pfu)
            events.append(SpaceWeatherEvent(
                external_id=f"{run[0].time:%Y-%m-%dT%H:%M}-GOES-P10",
                source="noaa_swpc", event_type="proton_flux",
                start_time=run[0].time, peak_time=peak.time, end_time=run[-1].time,
                intensity=round(peak.flux_pfu, 3), intensity_unit="pfu",
                raw={"s_scale": noaa_s_scale(peak.flux_pfu), "readings": len(run)},
            ))
        run.clear()

    for reading in sorted(readings, key=lambda r: r.time):
        if reading.flux_pfu >= threshold_pfu:
            run.append(reading)
        else:
            close_run()
    close_run()
    return events


class NoaaSwpcClient:
    """NOAA SWPC adapter: GOES >=10 MeV proton flux turned into proton events.

    NOAA only publishes the last 1, 3 or 7 days, so ``fetch`` ignores readings
    outside the requested range. For past missions, DONKI SEP events are the
    historical record.
    """

    name: Source = "noaa_swpc"
    WINDOWS: tuple[int, ...] = (1, 3, 7)

    def __init__(
        self,
        fetcher: JsonFetcher | None = None,
        base_url: str = NOAA_BASE_URL,
        window_days: int = 7,
    ) -> None:
        if window_days not in self.WINDOWS:
            raise ValueError(f"window_days must be one of {self.WINDOWS}")
        self._fetcher = fetcher or HttpJsonFetcher()
        self._url = f"{base_url}/integral-protons-{window_days}-day.json"

    def fetch_readings(self) -> list[ProtonReading]:
        """Downloads the >=10 MeV readings of the current window."""
        readings: list[ProtonReading] = []
        for item in self._fetcher(self._url, None) or []:
            if item.get("energy") != PROTON_ENERGY or item.get("flux") is None:
                continue
            moment = parse_time(item.get("time_tag"))
            if moment is not None:
                readings.append(ProtonReading(time=moment, flux_pfu=float(item["flux"])))
        return readings

    def fetch(self, start: date, end: date) -> list[SpaceWeatherEvent]:
        readings = [r for r in self.fetch_readings() if start <= r.time.date() <= end]
        return detect_proton_events(readings)


# =====================================================================
# 5. SYNC SERVICE (fetch -> cache)
# =====================================================================

@dataclass
class SyncReport:
    """Summary of a synchronization run."""

    fetched: dict[str, int] = field(default_factory=dict)
    inserted: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def default_sources() -> list[SpaceWeatherSource]:
    """Sources used by the app: DONKI and NOAA SWPC."""
    return [DonkiClient(), NoaaSwpcClient()]


DEFAULT_SYNC_TIMEOUT_S: float = 45.0


def _fetch_with_deadline(
    sources: Sequence[SpaceWeatherSource], start: date, end: date, timeout_s: float
) -> dict[str, list[SpaceWeatherEvent] | Exception | None]:
    """Runs every ``source.fetch`` in parallel with a hard wall-clock limit.

    Uses daemon threads, so a request that hangs is abandoned at the deadline and
    never keeps the program alive. Sources still running map to ``None``.
    """
    results: dict[str, list[SpaceWeatherEvent] | Exception | None] = {s.name: None for s in sources}
    lock = threading.Lock()

    def run(source: SpaceWeatherSource) -> None:
        try:
            outcome: list[SpaceWeatherEvent] | Exception = source.fetch(start, end)
        except Exception as exc:  # Reported, never raised from a worker thread
            outcome = exc
        with lock:
            results[source.name] = outcome

    threads = [threading.Thread(target=run, args=(s,), daemon=True) for s in sources]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_s
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with lock:
        return dict(results)


def sync_space_weather(
    repos: Any,
    start: date,
    end: date | None = None,
    sources: Sequence[SpaceWeatherSource] | None = None,
    timeout_s: float = DEFAULT_SYNC_TIMEOUT_S,
) -> SyncReport:
    """Downloads events from every source and stores the new ones.

    Sources run in parallel and the whole call never takes longer than
    ``timeout_s``. A failing or slow source does not stop the others: its error
    goes to the report, and the events it had already gathered are still stored.

    Args:
        repos: ``Repositorios`` from ``services.database.sesion()``.
        start: First date to query.
        end: Last date to query. Defaults to today (UTC).
        sources: Sources to use. Defaults to ``default_sources()``.
        timeout_s: Hard limit for the whole download, in seconds.

    Returns:
        ``SyncReport`` with counts per source and any errors.
    """
    end = end or datetime.now(UTC).date()
    if end < start:
        raise ValueError("end must be on or after start.")

    report = SyncReport()
    active = list(sources) if sources is not None else default_sources()
    outcomes = _fetch_with_deadline(active, start, end, timeout_s)

    for source in active:
        outcome = outcomes[source.name]
        if outcome is None:
            report.errors[source.name] = f"no answer within {timeout_s:.0f} s"
            events = list(getattr(source, "collected", []))
            if not events:
                continue
        elif isinstance(outcome, Exception):
            report.errors[source.name] = str(outcome)
            logger.warning("Space weather source %s failed: %s", source.name, outcome)
            continue
        else:
            events = outcome
        for part, error in getattr(source, "partial_errors", {}).items():
            report.errors[f"{source.name}:{part}"] = error
        report.fetched[source.name] = len(events)
        report.inserted[source.name] = repos.clima_espacial.insertar_nuevos(
            [event.to_row() for event in events]
        )
    return report


def solar_event_days(
    repos: Any,
    start: date,
    end: date,
    event_types: Sequence[str] = ("SEP", "proton_flux"),
) -> dict[date, str]:
    """Days with a cached solar particle event, mapped to the event type.

    Used by the data generator to set ``solar_event_flag`` on real event days.
    """
    begin = datetime.combine(start, datetime.min.time())
    finish = datetime.combine(end, datetime.max.time()).replace(microsecond=0)
    days: dict[date, str] = {}
    for event_type in event_types:
        for row in repos.clima_espacial.entre(begin, finish, tipo=event_type):
            first = datetime.fromisoformat(row["start_time"]).date()
            last = datetime.fromisoformat(row["end_time"]).date() if row.get("end_time") else first
            for offset in range((last - first).days + 1):
                days.setdefault(first + timedelta(days=offset), event_type)
    return days
