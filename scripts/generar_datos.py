"""Generates the demo mission and fills ``data/astro.db``.

What it creates:
    * Users: 4 fictional astronauts, 2 flight surgeons and 1 admin.
    * One mission (LEO / ISS by default), mission logs and crew assignments.
    * Pills and meals (base for reminders).
    * Measurements following the team's sampling plan:
        - Watch: every 15 min (HR, HRV, BP, SpO2, RR, skin temp, sleep stage, ENMO).
        - Glucose: 2 days per week, 4 readings (fasting + 2 h after each meal).
        - Hemoglobin: weekly.
        - Saliva: 1 day per week, 4 samples (wake, +30 min, afternoon, bedtime).
        - Body composition chair: weekly, fasting.
        - Radiation: daily, with real solar events from the cache (DONKI / NOAA).
        - Menstrual cycle for female astronauts.
    * ``reference_values`` with every literature value used, and its source.

Demo cases (one per astronaut, visible in the dashboard):
    * Astronaut 1: one post-meal hyperglycemia (S1).
    * Astronaut 2: early infection on days 30-33 (G1).
    * Astronaut 3: circadian misalignment on days 20-27 (F1).
    * Astronaut 4: progressive mass and muscle loss (C1/C2).

Patterns:
    * **Strategy + Registry**: each table has its own generator class
      (``MeasurementGenerator``). Adding a measurement means adding a class to
      ``GENERATORS``. Each demo case is a ``Scenario`` that modifies physiology.
    * **Reproducible**: the same ``--seed`` always produces the same data.

Daily reports and alerts are NOT generated here: ``services/reportes.py`` computes
them from these measurements.

Run from the repo root::

    python -m scripts.generar_datos                  # 50 days, ISS, real NASA events if online
    python -m scripts.generar_datos --days 14 --no-sync
    python -m scripts.generar_datos --environment lunar --seed 7
"""

from __future__ import annotations

import argparse
import logging
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, ClassVar

from config import DB_PATH, SCHEMA_PATH
from core.ecuaciones import convert_dhea_pg_ml_to_nmol_l
from core.enums import Rol, Sexo
from scripts.crear_db import crear_base_datos
from services.database import Repositorios, sesion
from services.radiacion_api import solar_event_days, sync_space_weather

# =====================================================================
# 1. LITERATURE PARAMETERS (all stored in reference_values)
# =====================================================================

# Daily dose by environment (mSv/day). ISS: midpoint of 0.4-0.7 (team document).
DOSE_RATE_MSV_DAY: dict[str, float] = {"iss": 0.55, "lunar": 1.369, "deep_space": 1.8}

# Team assumption: on a solar particle event day the dose is multiplied by this factor.
SPE_DOSE_FACTOR: float = 2.5

ISS_MEAN_SLEEP_H: float = 6.09        # Barger 2014
CAR_MEAN_INCREASE: float = 0.50       # Wüst 2000: about +50 % at 30 min
FEVER_HR_BPM_PER_C: float = 8.5       # Karjalainen 1986
LUTEAL_SKIN_TEMP_C: float = 0.3       # Physiological luteal rise (+0.3 to +0.5 °C)

# Day-to-day variability of nocturnal values (team assumption, in line with the ~10 %
# day-to-day variation of ln RMSSD reported for healthy adults). Without it the
# personal baselines would have an unrealistically small standard deviation.
DAILY_HRV_LOG_SD: float = 0.10
DAILY_HR_SD_BPM: float = 1.8
DAILY_SKIN_TEMP_SD_C: float = 0.08
DAILY_RR_SD_RPM: float = 0.4

REFERENCE_VALUES: tuple[dict[str, Any], ...] = (
    {"parameter": "sleep_hours", "context": "iss_mean", "value": 6.09, "unit": "h",
     "source_name": "Barger LK et al. Lancet Neurol 2014",
     "source_url": "https://doi.org/10.1016/S1474-4422(14)70122-X"},
    {"parameter": "sleep_hours", "context": "recommended_min", "value": 7.0, "unit": "h",
     "source_name": "Watson NF et al. Sleep 2015 (AASM/SRS)",
     "source_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC4434546/"},
    {"parameter": "hrv_rmssd_ms", "context": "healthy_adult_mean", "value": 42.0, "unit": "ms",
     "source_name": "Nunan D et al. PACE 2010",
     "source_url": "https://doi.org/10.1111/j.1540-8159.2010.02841.x"},
    {"parameter": "hrv_sdnn_ms", "context": "healthy_adult_mean", "value": 50.0, "unit": "ms",
     "source_name": "Nunan D et al. PACE 2010",
     "source_url": "https://doi.org/10.1111/j.1540-8159.2010.02841.x"},
    {"parameter": "car_pct", "context": "mean_increase_30min", "value": 50.0, "unit": "%",
     "sample_size": 509, "source_name": "Wüst S et al. Noise Health 2000",
     "source_url": "https://pubmed.ncbi.nlm.nih.gov/12689474/"},
    {"parameter": "salivary_alpha_amylase_u_ml", "context": "adult_mean", "value": 92.4,
     "unit": "U/mL", "source_name": "Salimetrics example ranges",
     "source_url": "https://salimetrics.com/analyte/salivary-alpha-amylase"},
    {"parameter": "fever_hr_increase", "context": "per_degree_c", "value": 8.5, "unit": "bpm/°C",
     "source_name": "Karjalainen J, Viitasalo M. Arch Intern Med 1986",
     "source_url": "https://pubmed.ncbi.nlm.nih.gov/2424378/"},
    {"parameter": "radiation_dose_msv_day", "context": "iss", "value": 0.731, "unit": "mSv/day",
     "source_name": "Zhang S et al. Sci Adv 2020",
     "source_url": "https://doi.org/10.1126/sciadv.aaz1334"},
    {"parameter": "radiation_dose_msv_day", "context": "lunar_surface", "value": 1.369,
     "unit": "mSv/day", "source_name": "Zhang S et al. Sci Adv 2020",
     "source_url": "https://doi.org/10.1126/sciadv.aaz1334"},
    {"parameter": "radiation_dose_msv_day", "context": "deep_space_transit", "value": 1.8,
     "unit": "mSv/day", "source_name": "Hassler DM et al. Science 2014",
     "source_url": "https://doi.org/10.1126/science.1244797"},
    {"parameter": "radiation_limit_msv", "context": "career", "value": 600.0, "unit": "mSv",
     "source_name": "NASA OCHMO, NASA-STD-3001",
     "source_url": "https://www.nasa.gov/wp-content/uploads/2023/03/radiation-protection-technical-brief-ochmo.pdf"},
)


# =====================================================================
# 2. CONFIGURATION
# =====================================================================

@dataclass(frozen=True)
class AstronautProfile:
    """Fictional astronaut and their personal physiological baseline."""

    full_name: str
    email: str
    sex: Sexo
    birth_date: date
    height_cm: float
    weight_kg: float
    muscle_pct: float
    fat_pct: float
    visceral_fat_level: float
    resting_hr_bpm: float
    rmssd_ms: float
    baseline_hb_g_dl: float
    scenario: str = "none"
    cycle_offset_days: int = 0


@dataclass(frozen=True)
class MissionConfig:
    """Parameters of the generated mission."""

    name: str = "Space Oddity LEO-1"
    environment: str = "iss"
    start_date: date = date(2024, 5, 1)  # Includes the real May 2024 solar storms
    days: int = 50
    seed: int = 42


DEFAULT_MISSION = MissionConfig()

CREW: tuple[AstronautProfile, ...] = (
    AstronautProfile("Andrea Solís", "andrea.solis@spaceoddity.dev", Sexo.FEMENINO,
                     date(1988, 4, 12), 165.0, 61.0, 34.0, 24.0, 6, 62, 45, 13.8,
                     scenario="glycemic", cycle_offset_days=3),
    AstronautProfile("Mateo Rojas", "mateo.rojas@spaceoddity.dev", Sexo.MASCULINO,
                     date(1985, 9, 3), 178.0, 76.0, 47.0, 17.0, 8, 58, 48, 15.2,
                     scenario="infection"),
    AstronautProfile("Valeria Mora", "valeria.mora@spaceoddity.dev", Sexo.FEMENINO,
                     date(1991, 1, 27), 170.0, 64.0, 35.0, 23.0, 5, 60, 52, 13.5,
                     scenario="circadian", cycle_offset_days=17),
    AstronautProfile("Daniel Vargas", "daniel.vargas@spaceoddity.dev", Sexo.MASCULINO,
                     date(1983, 6, 18), 182.0, 82.0, 48.0, 19.0, 9, 64, 38, 14.9,
                     scenario="atrophy"),
)

DOCTORS: tuple[tuple[str, str], ...] = (
    ("Dra. Laura Jiménez", "laura.jimenez@spaceoddity.dev"),
    ("Dr. Esteban Quesada", "esteban.quesada@spaceoddity.dev"),
)
ADMIN: tuple[str, str] = ("Administración Space Oddity", "admin@spaceoddity.dev")


# =====================================================================
# 3. SCENARIOS (demo cases)
# =====================================================================

@dataclass(frozen=True)
class Modifiers:
    """Physiological changes a scenario applies on a given day."""

    temp_c: float = 0.0
    hr_bpm: float = 0.0
    hrv_factor: float = 1.0
    rr_rpm: float = 0.0
    spo2_pct: float = 0.0
    sleep_h: float | None = None
    blunted_car: bool = False
    high_night_cortisol: bool = False
    weight_loss_per_day: float = 0.0004   # ~2 % in 50 days (baseline in flight)
    muscle_loss_pct_per_day: float = 0.01
    hyperglycemia: bool = False


class Scenario:
    """Base scenario: healthy astronaut."""

    name: ClassVar[str] = "none"

    def modifiers(self, day: int) -> Modifiers:
        return Modifiers()


class InfectionScenario(Scenario):
    """Fever with tachycardia, low HRV and fast breathing on days 30-33."""

    name = "infection"
    DAYS = range(30, 34)

    def modifiers(self, day: int) -> Modifiers:
        if day not in self.DAYS:
            return Modifiers()
        severity = 0.6 + 0.1 * (day - self.DAYS.start)          # 0.6 -> 0.9 °C
        return Modifiers(
            temp_c=severity, hr_bpm=FEVER_HR_BPM_PER_C * severity + 14, hrv_factor=0.55,
            rr_rpm=6.5, spo2_pct=-1.0,
        )


class CircadianScenario(Scenario):
    """Short sleep, blunted CAR and high night cortisol on days 20-27."""

    name = "circadian"
    DAYS = range(20, 28)

    def modifiers(self, day: int) -> Modifiers:
        if day not in self.DAYS:
            return Modifiers()
        return Modifiers(
            sleep_h=4.6, hrv_factor=0.75, hr_bpm=4, blunted_car=True, high_night_cortisol=True,
        )


class AtrophyScenario(Scenario):
    """Faster mass and muscle loss during the whole mission (~8 % mass in 50 days)."""

    name = "atrophy"

    def modifiers(self, day: int) -> Modifiers:
        return Modifiers(weight_loss_per_day=0.0016, muscle_loss_pct_per_day=0.06)


class GlycemicScenario(Scenario):
    """One post-meal hyperglycemia on day 15."""

    name = "glycemic"

    def modifiers(self, day: int) -> Modifiers:
        return Modifiers(hyperglycemia=day == 15)


SCENARIOS: dict[str, Scenario] = {
    s.name: s for s in (Scenario(), InfectionScenario(), CircadianScenario(),
                        AtrophyScenario(), GlycemicScenario())
}


# =====================================================================
# 4. GENERATION CONTEXT
# =====================================================================

@dataclass
class AstronautContext:
    """Everything a generator needs for one astronaut."""

    config: MissionConfig
    profile: AstronautProfile
    id_user: int
    id_mission_log: int
    rng: random.Random
    solar_days: dict[date, str] = field(default_factory=dict)

    @property
    def scenario(self) -> Scenario:
        return SCENARIOS[self.profile.scenario]

    def date_of(self, day: int) -> date:
        return self.config.start_date + timedelta(days=day - 1)

    def at(self, day: int, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(self.date_of(day), time(hour, minute), tzinfo=UTC)

    def days(self) -> range:
        return range(1, self.config.days + 1)

    def is_luteal(self, day: int) -> bool:
        return self.profile.sex == Sexo.FEMENINO and self.cycle_day(day) >= 16

    def cycle_day(self, day: int) -> int:
        return (day - 1 + self.profile.cycle_offset_days) % 28

    def gauss(self, mean: float, std: float, low: float = -math.inf, high: float = math.inf) -> float:
        return min(high, max(low, self.rng.gauss(mean, std)))


# =====================================================================
# 5. MEASUREMENT GENERATORS (Strategy + Registry)
# =====================================================================

class MeasurementGenerator:
    """Generates the rows of one table for one astronaut."""

    repo: ClassVar[str]

    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        raise NotImplementedError

    def save(self, repos: Repositorios, rows: Sequence[dict[str, Any]]) -> int:
        return getattr(repos, self.repo).insertar_varios(rows)


@dataclass(frozen=True)
class DailyOffsets:
    """Random shift of one day applied to every watch block of that day."""

    hr: float
    hrv_factor: float
    temp: float
    rr: float


class CardiovascularGenerator(MeasurementGenerator):
    """Watch data every 15 min. Sleep window 22:00-06:00 UTC, exercise 08:00 and 15:00."""

    repo = "cardiovascular"
    BLOCKS_PER_DAY = 96
    SLEEP_START_BLOCK = 88     # 22:00
    SLEEP_WINDOW_BLOCKS = 32   # 8 h
    # Effect of each activity level: (HR delta, HRV factor, RR delta, ENMO mean)
    ACTIVITY = {0: (5, 1.0, 0.0, 8.0), 1: (15, 0.8, 1.5, 45.0),
                2: (45, 0.45, 6.0, 180.0), 3: (65, 0.3, 9.0, 420.0)}

    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day in ctx.days():
            mods = ctx.scenario.modifiers(day)
            sleep_stages = self._sleep_stages(ctx, mods)
            luteal = LUTEAL_SKIN_TEMP_C if ctx.is_luteal(day) else 0.0
            daily = DailyOffsets(
                hr=ctx.rng.gauss(0, DAILY_HR_SD_BPM),
                hrv_factor=math.exp(ctx.rng.gauss(0, DAILY_HRV_LOG_SD)),
                temp=ctx.rng.gauss(0, DAILY_SKIN_TEMP_SD_C),
                rr=ctx.rng.gauss(0, DAILY_RR_SD_RPM),
            )
            start = ctx.at(day, 0)
            for block in range(self.BLOCKS_PER_DAY):
                moment = start + timedelta(minutes=15 * block)
                stage = sleep_stages.get((block - self.SLEEP_START_BLOCK) % self.BLOCKS_PER_DAY, 0)
                in_window = block >= self.SLEEP_START_BLOCK or block < 24
                asleep = in_window and stage > 0
                intensity = 0 if in_window else self._activity(ctx, day, moment.hour)
                rows.append(self._row(ctx, day, moment, stage, asleep, intensity, mods, luteal, daily))
        return rows

    def _sleep_stages(self, ctx: AstronautContext, mods: Modifiers) -> dict[int, int]:
        """Stage per block of the night (0 awake, 1 light, 2 deep, 3 REM)."""
        hours = mods.sleep_h
        if hours is None:
            hours = ctx.gauss(ISS_MEAN_SLEEP_H + 0.3, 0.45, 5.0, 7.5)
        asleep_blocks = min(self.SLEEP_WINDOW_BLOCKS, round(hours * 4))
        latency = (self.SLEEP_WINDOW_BLOCKS - asleep_blocks) // 2
        stages: dict[int, int] = {}
        for i in range(asleep_blocks):
            cycle, position = divmod(i, 6)                       # 90-min cycles
            early = cycle < 2
            pattern = (1, 2, 2, 1, 3, 1) if early else (1, 1, 3, 3, 1, 2)
            stages[latency + i] = pattern[position]
        return stages

    def _activity(self, ctx: AstronautContext, day: int, hour: int) -> int:
        exercise_day = day % 7 != 0
        if exercise_day and hour == 8:
            return 2                                             # Aerobic (cycle/treadmill)
        if exercise_day and hour == 15:
            return 3                                             # Resistance
        return 1 if ctx.rng.random() < 0.7 else 0

    def _row(self, ctx: AstronautContext, day: int, moment: datetime, stage: int, asleep: bool,
             intensity: int, mods: Modifiers, luteal: float, daily: DailyOffsets) -> dict[str, Any]:
        p = ctx.profile
        hr_delta, hrv_factor, rr_delta, enmo_mean = self.ACTIVITY[intensity]
        if asleep:
            hr_delta, hrv_factor, rr_delta, enmo_mean = -6, 1.2, -1.5, 2.0

        hr = ctx.gauss(p.resting_hr_bpm + hr_delta + mods.hr_bpm + daily.hr, 2.5, 40, 190)
        rmssd = (p.rmssd_ms * hrv_factor * mods.hrv_factor * daily.hrv_factor
                 * math.exp(ctx.rng.gauss(0, 0.12)))
        systolic = ctx.gauss(114 + 7 * intensity - (6 if asleep else 0), 4, 90, 200)
        diastolic = ctx.gauss(73 + 2.5 * intensity - (4 if asleep else 0), 3, 50, 125)
        skin_base = 33.6 + (0.8 if asleep else 0.0)
        skin = ctx.gauss(skin_base + luteal + mods.temp_c + daily.temp, 0.15)
        sample_block = asleep and moment.hour == 2 and moment.minute == 0
        rr_intervals = self._rr_sample(ctx, hr, rmssd) if sample_block else None

        return {
            "id_mission_log": ctx.id_mission_log, "mission_day": day, "recorded_at": moment,
            "heart_rate_bpm": round(hr, 1),
            "hrv_rmssd_ms": round(max(rmssd, 3.0), 1),
            "hrv_sdnn_ms": round(max(rmssd * ctx.gauss(1.2, 0.08, 1.0), 4.0), 1),
            "rr_mean_ms": round(60000.0 / hr, 1),
            "rr_intervals_sample_json": rr_intervals,
            "estimated_systolic_bp_mmhg": round(systolic),
            "estimated_diastolic_bp_mmhg": round(diastolic),
            "estimated_map_mmhg": round(diastolic + (systolic - diastolic) / 3, 1),
            "spo2_pct": round(ctx.gauss(97.8 + mods.spo2_pct, 0.6, 85, 100), 1),
            "perfusion_index_pct": round(ctx.gauss(3.0, 0.8, 0.5, 10), 2),
            "respiratory_rate_rpm": round(ctx.gauss(14 + rr_delta + mods.rr_rpm + daily.rr, 1.0, 8, 40), 1),
            "skin_temperature_c": round(skin, 2),
            "skin_temp_deviation_c": round(skin - skin_base, 2),
            "sleep_stage": stage if asleep else 0,
            "enmo_mg": round(max(ctx.rng.gauss(enmo_mean, enmo_mean * 0.25), 0.0), 1),
            "activity_intensity_level": 0 if asleep else intensity,
        }

    @staticmethod
    def _rr_sample(ctx: AstronautContext, hr: float, rmssd: float, n: int = 30) -> list[float]:
        """30 RR intervals whose RMSSD is close to ``rmssd`` (for validating the equations)."""
        mean_rr = 60000.0 / hr
        step = rmssd / math.sqrt(2)
        values = [mean_rr]
        for _ in range(n - 1):
            values.append(0.6 * values[-1] + 0.4 * mean_rr + ctx.rng.gauss(0, step))
        return [round(v, 1) for v in values]


class BloodGenerator(MeasurementGenerator):
    """Glucose 2 days/week (fasting + 3 post-meal) and weekly hemoglobin."""

    repo = "sangre"
    GLUCOSE_DAYS = (1, 4)       # day % 7
    POST_MEAL_TIMES = ((9, 30), (14, 30), (20, 30))

    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day in ctx.days():
            mods = ctx.scenario.modifiers(day)
            if day % 7 in self.GLUCOSE_DAYS or mods.hyperglycemia:
                rows.append(self._glucose(ctx, day, (6, 30), "fasting", ctx.gauss(88, 6, 65, 125)))
                for i, moment in enumerate(self.POST_MEAL_TIMES):
                    spike = mods.hyperglycemia and i == 1
                    value = ctx.gauss(195, 6) if spike else ctx.gauss(124, 14, 80, 175)
                    rows.append(self._glucose(ctx, day, moment, "post_meal_2h", value))
            if day % 7 == 0:
                hb = ctx.profile.baseline_hb_g_dl - 0.02 * day + ctx.rng.gauss(0, 0.2)
                rows.append({
                    "id_mission_log": ctx.id_mission_log, "mission_day": day,
                    "recorded_at": ctx.at(day, 6, 20), "hemoglobin_g_dl": round(hb, 2),
                    "measurement_context": "fasting",
                })
        return rows

    @staticmethod
    def _glucose(ctx: AstronautContext, day: int, hm: tuple[int, int], context: str,
                 value: float) -> dict[str, Any]:
        return {
            "id_mission_log": ctx.id_mission_log, "mission_day": day,
            "recorded_at": ctx.at(day, *hm), "blood_glucose_mg_dl": round(value, 1),
            "measurement_context": context,
        }


class SalivaGenerator(MeasurementGenerator):
    """One day per week: 4 samples with the normal diurnal cortisol curve."""

    repo = "saliva"
    SALIVA_DAY = 3               # day % 7

    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day in ctx.days():
            if day % 7 != self.SALIVA_DAY:
                continue
            mods = ctx.scenario.modifiers(day)
            wake = ctx.gauss(13.0, 1.8, 8, 20)
            if mods.blunted_car:
                car_factor = ctx.gauss(1.12, 0.04)
            else:
                car_factor = ctx.gauss(1 + CAR_MEAN_INCREASE, 0.12, 1.2)
            night = ctx.gauss(5.6, 0.5) if mods.high_night_cortisol else ctx.gauss(2.4, 0.6, 0.8, 3.9)
            samples = (
                ("wake_0m", (6, 0), wake, 300.0, 45.0),
                ("wake_30m", (6, 30), wake * car_factor, 310.0, 55.0),
                ("afternoon", (16, 0), ctx.gauss(5.5, 1.0, 3.0, 8.0), 220.0, 110.0),
                ("bedtime", (21, 45), night, 180.0, 90.0),
            )
            for slot, hm, cortisol, dhea_pg, saa_mean in samples:
                dhea_pg_ml = ctx.gauss(dhea_pg, dhea_pg * 0.12, 40)
                rows.append({
                    "id_mission_log": ctx.id_mission_log, "mission_day": day,
                    "recorded_at": ctx.at(day, *hm), "collection_slot": slot,
                    "sample_volume_ul": int(ctx.gauss(1200, 200, 500)),
                    "salivary_cortisol_nmol_l": round(cortisol, 2),
                    "salivary_dhea_pg_ml": round(dhea_pg_ml, 1),
                    "salivary_dhea_nmol_l": round(convert_dhea_pg_ml_to_nmol_l(dhea_pg_ml), 3),
                    "salivary_alpha_amylase_u_ml": round(saa_mean * math.exp(ctx.rng.gauss(0, 0.35)), 1),
                })
        return rows


class BodyCompositionGenerator(MeasurementGenerator):
    """Weekly fasting measurement on the bioimpedance chair."""

    repo = "composicion"

    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        p, rows = ctx.profile, []
        for day in ctx.days():
            if day % 7 != 1:
                continue
            mods = ctx.scenario.modifiers(day)
            weight = p.weight_kg * (1 - mods.weight_loss_per_day * day) + ctx.rng.gauss(0, 0.2)
            rows.append({
                "id_mission_log": ctx.id_mission_log, "mission_day": day,
                "recorded_at": ctx.at(day, 6, 15), "weight_kg": round(weight, 2),
                "visceral_fat_level": round(ctx.gauss(p.visceral_fat_level, 0.3, 1), 1),
                "body_water_pct": round(ctx.gauss(57.5, 0.8), 1),
                "muscle_pct": round(
                    p.muscle_pct - mods.muscle_loss_pct_per_day * day + ctx.rng.gauss(0, 0.2), 2
                ),
                "fat_pct": round(ctx.gauss(p.fat_pct + 0.01 * day, 0.3), 2),
                "is_fasting": 1,
            })
        return rows


class RadiationGenerator(MeasurementGenerator):
    """Daily dose by environment. Real solar events raise the dose on their days.

    If the cache has no events for the mission dates, one synthetic SPE is placed
    on day 35 so the S4 alert can still be demonstrated.
    """

    repo = "radiacion"
    SYNTHETIC_SPE_DAY = 35

    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        base = DOSE_RATE_MSV_DAY[ctx.config.environment]
        synthetic = not ctx.solar_days
        cumulative, rows = 0.0, []
        for day in ctx.days():
            event_type = ctx.solar_days.get(ctx.date_of(day))
            if synthetic and day == self.SYNTHETIC_SPE_DAY:
                event_type = "SEP"
            dose = base * math.exp(ctx.rng.gauss(0, 0.08)) * (SPE_DOSE_FACTOR if event_type else 1.0)
            cumulative += dose
            if event_type is None:
                source = "synthetic_baseline"
            elif synthetic:
                source = "simulated"
            else:
                source = "noaa_swpc" if event_type == "proton_flux" else "nasa_donki"
            rows.append({
                "id_mission_log": ctx.id_mission_log, "mission_day": day,
                "recorded_at": ctx.at(day, 23, 59), "daily_dose_msv": round(dose, 4),
                "cumulative_dose_msv": round(cumulative, 4), "weighted_dose_msv": round(dose, 4),
                "solar_event_flag": int(event_type is not None), "solar_event_type": event_type,
                "data_source": source,
            })
        return rows


class MenstrualCycleGenerator(MeasurementGenerator):
    """28-day cycle for female astronauts."""

    repo = "ciclos"
    PHASES = ((5, "menstrual"), (13, "follicular"), (16, "ovulatory"), (28, "luteal"))

    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        if ctx.profile.sex != Sexo.FEMENINO:
            return []
        rows = []
        for day in ctx.days():
            cycle_day = ctx.cycle_day(day)
            phase = next(name for limit, name in self.PHASES if cycle_day < limit)
            rows.append({
                "id_user": ctx.id_user, "recorded_date": ctx.date_of(day),
                "menstruating": int(phase == "menstrual"), "cycle_phase": phase,
            })
        return rows


GENERATORS: tuple[MeasurementGenerator, ...] = (
    CardiovascularGenerator(),
    BloodGenerator(),
    SalivaGenerator(),
    BodyCompositionGenerator(),
    RadiationGenerator(),
    MenstrualCycleGenerator(),
)


# =====================================================================
# 6. ORCHESTRATION
# =====================================================================

def _seed_people_and_mission(db: Repositorios, config: MissionConfig) -> tuple[list[int], list[int], int]:
    """Creates users, mission, logs, assignments, pills and meals. Returns (doctors, logs, mission)."""
    db.usuarios.insertar(full_name=ADMIN[0], email=ADMIN[1], role=Rol.ADMIN,
                         birth_date=date(1980, 1, 1), sex=Sexo.FEMENINO)
    doctor_ids = [
        db.usuarios.insertar(full_name=name, email=email, role=Rol.DOCTOR,
                             birth_date=date(1978 + i, 3, 10),
                             sex=Sexo.FEMENINO if i == 0 else Sexo.MASCULINO)
        for i, (name, email) in enumerate(DOCTORS)
    ]
    id_mission = db.misiones.insertar(
        name=config.name, start_date=config.start_date,
        end_date=config.start_date + timedelta(days=config.days - 1),
        description=f"Misión de demostración ({config.environment}), {config.days} días.",
    )

    log_ids: list[int] = []
    for i, p in enumerate(CREW):
        id_user = db.usuarios.insertar(full_name=p.full_name, email=p.email, role=Rol.ASTRONAUTA,
                                       birth_date=p.birth_date, sex=p.sex)
        id_log = db.mission_logs.insertar(
            id_user=id_user, id_mission=id_mission, start_date=config.start_date,
            start_weight_kg=p.weight_kg, start_height_cm=p.height_cm, start_muscle_pct=p.muscle_pct,
            start_fat_pct=p.fat_pct, baseline_hb_g_dl=p.baseline_hb_g_dl,
        )
        doctor = doctor_ids[i // 2]                      # 2 astronauts per doctor
        db.asignaciones.insertar(id_doctor=doctor, id_mission_log=id_log)
        db.pastillas.insertar(
            id_user=id_user, assigned_by=doctor, name="Vitamina D3", dose="1000 UI",
            description="Suplemento diario estándar en vuelo.", scheduled_time=time(8, 0),
            start_date=config.start_date,
        )
        for name, meal_type, kcal, protein, hm in (
            ("Desayuno", "breakfast", 600, 30, (7, 0)), ("Almuerzo", "lunch", 800, 35, (12, 30)),
            ("Merienda", "snack", 300, 10, (16, 30)), ("Cena", "dinner", 800, 35, (19, 0)),
        ):
            db.comidas.insertar(id_user=id_user, name=name, meal_type=meal_type,
                                target_calories_kcal=kcal, target_protein_g=protein,
                                scheduled_time=time(*hm))
        log_ids.append(id_log)
    return doctor_ids, log_ids, id_mission


def _seed_reference_values(db: Repositorios) -> None:
    retrieved = datetime.now(UTC)
    db.referencias.insertar_varios([{**ref, "retrieved_at": retrieved} for ref in REFERENCE_VALUES])


def _sync_nasa(db_path: Path, start: date, end: date) -> None:
    """Downloads real space weather events for the mission dates into the cache."""
    with sesion(db_path) as db:
        report = sync_space_weather(db, start, end)
    for source, error in report.errors.items():
        print(f"[AVISO] {source} no disponible: {error[:150]}")
    for source, count in report.inserted.items():
        print(f"[OK] {source}: {count} eventos nuevos")


def generate_mission(
    config: MissionConfig = DEFAULT_MISSION,
    db_path: Path = DB_PATH,
    *,
    reset: bool = True,
    sync_nasa: bool = True,
) -> dict[str, int]:
    """Creates (or recreates) the database and fills it with the demo mission.

    Args:
        config: Mission parameters.
        db_path: Destination database.
        reset: Deletes the existing file first. With ``reset=False`` the space weather
            cache is kept (useful offline), but the database must not already
            contain a mission.
        sync_nasa: Downloads real space weather events before generating. Either way,
            the events already in the cache are used.

    Returns:
        Rows inserted per table.
    """
    if config.environment not in DOSE_RATE_MSV_DAY:
        raise ValueError(f"environment must be one of {sorted(DOSE_RATE_MSV_DAY)}")
    if reset:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    crear_base_datos(db_path, SCHEMA_PATH)

    end = config.start_date + timedelta(days=config.days - 1)
    if sync_nasa:
        try:
            _sync_nasa(db_path, config.start_date, end)
        except Exception as exc:  # Never block generation because of the network
            print(f"[AVISO] No se pudieron sincronizar eventos solares: {exc}")
    with sesion(db_path) as db:
        solar_days = solar_event_days(db, config.start_date, end)
    if solar_days:
        print(f"[OK] {len(solar_days)} días con eventos solares reales en la misión")
    else:
        print("[AVISO] Sin eventos solares reales en caché: se usa un evento simulado el día 35")

    counts: dict[str, int] = {}
    with sesion(db_path) as db:
        _, log_ids, _ = _seed_people_and_mission(db, config)
        _seed_reference_values(db)
        users = {u["email"]: u["id"] for u in db.usuarios.por_rol(Rol.ASTRONAUTA)}
        for profile, id_log in zip(CREW, log_ids, strict=True):
            ctx = AstronautContext(
                config=config, profile=profile, id_user=users[profile.email], id_mission_log=id_log,
                rng=random.Random(f"{config.seed}-{profile.email}"), solar_days=solar_days,
            )
            for generator in GENERATORS:
                table = type(generator).__name__.removesuffix("Generator")
                counts[table] = counts.get(table, 0) + generator.save(db, generator.generate(ctx))

    with sesion(db_path) as db:
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera la misión de demo en data/astro.db.")
    parser.add_argument("--days", type=int, default=MissionConfig.days)
    parser.add_argument("--start", type=date.fromisoformat, default=MissionConfig.start_date,
                        help="Fecha de inicio YYYY-MM-DD (por defecto 2024-05-01).")
    parser.add_argument("--environment", choices=sorted(DOSE_RATE_MSV_DAY), default="iss")
    parser.add_argument("--seed", type=int, default=MissionConfig.seed)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--no-sync", action="store_true", help="No consulta DONKI ni NOAA.")
    parser.add_argument("--keep", action="store_true", help="No borra la base existente.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)  # Los avisos de red ya se imprimen resumidos

    config = MissionConfig(environment=args.environment, start_date=args.start,
                           days=args.days, seed=args.seed)
    counts = generate_mission(config, args.db, reset=not args.keep, sync_nasa=not args.no_sync)
    print(f"[OK] Misión '{config.name}' generada ({config.days} días, {config.environment}):")
    for table, count in counts.items():
        print(f"     {table:<20} {count:>6} filas")


if __name__ == "__main__":
    main()
