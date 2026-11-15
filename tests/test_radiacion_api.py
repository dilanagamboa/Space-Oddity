"""Tests for services/radiacion_api.py. They never touch the network."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any

import pytest

from services.database import Repositorios
from services.radiacion_api import (
    DonkiClient,
    HostUnreachable,
    NoaaSwpcClient,
    ProtonReading,
    SpaceWeatherUnavailable,
    detect_proton_events,
    flare_class_to_flux,
    noaa_s_scale,
    parse_time,
    solar_event_days,
    sync_space_weather,
)

# Responses with the real field names (CCMC DONKI docs, NOAA SWPC JSON)
DONKI_RESPONSES: dict[str, list[dict[str, Any]]] = {
    "SEP": [{
        "sepID": "2024-05-11T02:15:00-SEP-001", "eventTime": "2024-05-11T02:15Z",
        "instruments": [{"displayName": "GOES-P: SEISS >10 MeV"}], "linkedEvents": [],
    }],
    "FLR": [{
        "flrID": "2024-05-10T06:27:00-FLR-001", "beginTime": "2024-05-10T06:27Z",
        "peakTime": "2024-05-10T06:54Z", "endTime": "2024-05-10T07:06Z", "classType": "X3.9",
    }],
    "CME": [{
        "activityID": "2024-05-10T07:12:00-CME-001", "startTime": "2024-05-10T07:12Z",
        "cmeAnalyses": [{"speed": 1100.0}, {"speed": 1250.0}],
    }],
    "GST": [{
        "gstID": "2024-05-10T15:00:00-GST-001", "startTime": "2024-05-10T15:00Z",
        "allKpIndex": [{"kpIndex": 8.0}, {"kpIndex": 9.0}],
    }],
}


def fake_donki(url: str, params: Any) -> Any:
    return DONKI_RESPONSES[url.rsplit("/", 1)[-1]]


def noaa_payload(fluxes: list[float]) -> list[dict[str, Any]]:
    rows = []
    for i, flux in enumerate(fluxes):
        stamp = f"2024-05-11T02:{i * 5:02d}:00Z"
        rows.append({"time_tag": stamp, "satellite": 18, "flux": flux, "energy": ">=10 MeV"})
        rows.append({"time_tag": stamp, "satellite": 18, "flux": flux * 10, "energy": ">=1 MeV"})
    return rows


class FailingSource:
    name = "nasa_donki"

    def fetch(self, start: date, end: date) -> list:
        raise SpaceWeatherUnavailable("offline")


# --- Pure helpers -------------------------------------------------------------
def test_parse_time_handles_donki_and_noaa_formats() -> None:
    assert parse_time("2024-05-10T06:27Z") == datetime(2024, 5, 10, 6, 27, tzinfo=UTC)
    assert parse_time("2024-05-10T06:27:00Z") == datetime(2024, 5, 10, 6, 27, tzinfo=UTC)
    assert parse_time(None) is None


@pytest.mark.parametrize(
    ("class_type", "flux"), [("X3.9", 3.9e-4), ("M1.6", 1.6e-5), ("C1", 1e-6), ("Z1", None)]
)
def test_flare_class_to_flux(class_type: str, flux: float | None) -> None:
    result = flare_class_to_flux(class_type)
    assert result == pytest.approx(flux) if flux else result is None


@pytest.mark.parametrize(
    ("pfu", "level"), [(5.0, None), (10.0, "S1"), (150.0, "S2"), (2e4, "S4")]
)
def test_noaa_s_scale(pfu: float, level: str | None) -> None:
    assert noaa_s_scale(pfu) == level


# --- Proton events -------------------------------------------------------------
def _readings(fluxes: list[float]) -> list[ProtonReading]:
    return [
        ProtonReading(datetime(2024, 5, 11, 2, i * 5, tzinfo=UTC), f) for i, f in enumerate(fluxes)
    ]


def test_proton_event_needs_three_consecutive_readings() -> None:
    assert detect_proton_events(_readings([3, 12, 15, 4, 11, 2])) == []
    (event,) = detect_proton_events(_readings([3, 12, 150, 40, 2]))
    assert event.intensity == 150 and event.raw["s_scale"] == "S2"
    assert event.start_time.minute == 5 and event.end_time.minute == 15


# --- Adapters --------------------------------------------------------------------
def test_donki_adapter_maps_every_event_type() -> None:
    events = DonkiClient(fetcher=fake_donki, api_key="TEST").fetch(date(2024, 5, 1), date(2024, 5, 31))
    by_type = {e.event_type: e for e in events}
    assert set(by_type) == {"SEP", "FLR", "CME", "GST"}
    assert by_type["FLR"].intensity == pytest.approx(3.9e-4)
    assert by_type["CME"].intensity == 1250.0
    assert by_type["GST"].intensity == 9.0


def test_donki_falls_back_to_second_url() -> None:
    calls: list[str] = []

    def flaky(url: str, params: Any) -> Any:
        calls.append(url)
        if "kauai" in url:
            raise SpaceWeatherUnavailable("down")
        assert params["api_key"] == "TEST"
        return fake_donki(url, params)

    client = DonkiClient(fetcher=flaky, api_key="TEST", event_types=("SEP",))
    assert len(client.fetch(date(2024, 5, 1), date(2024, 5, 31))) == 1
    assert any("api.nasa.gov" in c for c in calls)


def test_donki_keeps_working_types_when_one_fails(db: Repositorios) -> None:
    def no_flares(url: str, params: Any) -> Any:
        if url.endswith("/FLR"):
            raise SpaceWeatherUnavailable("timeout")
        return fake_donki(url, params)

    client = DonkiClient(fetcher=no_flares, api_key="TEST")
    report = sync_space_weather(db, date(2024, 5, 1), date(2024, 5, 31), [client])
    assert report.inserted == {"nasa_donki": 3}                 # SEP, CME, GST kept
    assert set(report.errors) == {"nasa_donki:FLR"}
    assert solar_event_days(db, date(2024, 5, 1), date(2024, 5, 31)) == {date(2024, 5, 11): "SEP"}


def test_dead_url_is_tried_only_once() -> None:
    calls: list[str] = []

    def kauai_dead(url: str, params: Any) -> Any:
        calls.append(url)
        if "kauai" in url:
            raise HostUnreachable("connection refused")
        return fake_donki(url, params)

    events = DonkiClient(fetcher=kauai_dead, api_key="T").fetch(date(2024, 5, 1), date(2024, 5, 31))
    assert len(events) == 4
    assert sum("kauai" in c for c in calls) == 1                # not once per event type


def test_time_budget_keeps_events_already_fetched() -> None:
    client = DonkiClient(fetcher=fake_donki, api_key="T", time_budget_s=0.0)
    with pytest.raises(SpaceWeatherUnavailable, match="time budget"):
        client.fetch(date(2024, 5, 1), date(2024, 5, 31))


def test_donki_raises_only_if_every_type_fails() -> None:
    def down(url: str, params: Any) -> Any:
        raise SpaceWeatherUnavailable("down")

    with pytest.raises(SpaceWeatherUnavailable):
        DonkiClient(fetcher=down, api_key="T").fetch(date(2024, 5, 1), date(2024, 5, 2))


def test_noaa_adapter_keeps_only_10_mev_in_range() -> None:
    client = NoaaSwpcClient(fetcher=lambda url, p: noaa_payload([2, 20, 30, 25, 3]))
    (event,) = client.fetch(date(2024, 5, 11), date(2024, 5, 11))
    assert event.intensity == 30 and event.source == "noaa_swpc"
    assert client.fetch(date(2024, 6, 1), date(2024, 6, 2)) == []


# --- Sync service + cache --------------------------------------------------------
def test_sync_stores_events_once(db: Repositorios) -> None:
    sources = [
        DonkiClient(fetcher=fake_donki, api_key="TEST"),
        NoaaSwpcClient(fetcher=lambda url, p: noaa_payload([2, 20, 30, 25, 3])),
    ]
    first = sync_space_weather(db, date(2024, 5, 1), date(2024, 5, 31), sources)
    assert first.ok and first.inserted == {"nasa_donki": 4, "noaa_swpc": 1}

    second = sync_space_weather(db, date(2024, 5, 1), date(2024, 5, 31), sources)
    assert second.inserted == {"nasa_donki": 0, "noaa_swpc": 0}

    stored = db.clima_espacial.entre(datetime(2024, 5, 1), datetime(2024, 5, 31), tipo="FLR")
    assert stored[0]["raw_json"]["classType"] == "X3.9"


def test_sync_keeps_going_when_a_source_fails(db: Repositorios) -> None:
    sources = [FailingSource(), NoaaSwpcClient(fetcher=lambda url, p: noaa_payload([20, 30, 25]))]
    report = sync_space_weather(db, date(2024, 5, 1), date(2024, 5, 31), sources)
    assert not report.ok and "nasa_donki" in report.errors
    assert report.inserted == {"noaa_swpc": 1}


def test_sync_never_exceeds_timeout_and_keeps_partial_events(db: Repositorios) -> None:
    def hangs_after_sep(url: str, params: Any) -> Any:
        if url.endswith("/SEP"):
            return fake_donki(url, params)
        time.sleep(5)                                              # FLR hangs
        return []

    client = DonkiClient(fetcher=hangs_after_sep, api_key="T")
    started = time.monotonic()
    report = sync_space_weather(db, date(2024, 5, 1), date(2024, 5, 31), [client], timeout_s=0.5)
    assert time.monotonic() - started < 1.5
    assert "nasa_donki" in report.errors
    assert report.inserted == {"nasa_donki": 1}                    # SEP kept anyway


def test_solar_event_days_reads_cache(db: Repositorios) -> None:
    sync_space_weather(
        db, date(2024, 5, 1), date(2024, 5, 31), [DonkiClient(fetcher=fake_donki, api_key="T")]
    )
    days = solar_event_days(db, date(2024, 5, 1), date(2024, 5, 31))
    assert days == {date(2024, 5, 11): "SEP"}
