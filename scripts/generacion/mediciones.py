"""Measurement generators (Strategy + Registry): one class per table.

Each generator receives an ``AstronautContext`` and returns the rows of its table.
Schedules come from ``core.protocolo`` so they match the reminders service.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, ClassVar

from core.ecuaciones import convert_dhea_pg_ml_to_nmol_l
from core.enums import Sexo
from core.escenarios import BLUNTED_CAR_RATIO, HIGH_NIGHT_CORTISOL_NMOL_L, SPE_DOSE_FACTOR
from core.protocolo import (
    AEROBIC_EXERCISE_HOUR,
    CHAIR_AT,
    CHAIR_WEEKDAY,
    GLUCOSE_FASTING_AT,
    GLUCOSE_POST_MEAL_AT,
    GLUCOSE_WEEKDAYS,
    HEMOGLOBIN_AT,
    HEMOGLOBIN_WEEKDAY,
    RESISTANCE_EXERCISE_HOUR,
    REST_WEEKDAY,
    SALIVA_AT,
    SALIVA_WEEKDAY,
    SLEEP_WINDOW,
    HourMinute,
    mission_date,
    mission_moment,
    weekday,
)
from scripts.generacion.escenarios import SCENARIOS, Modifiers, Scenario
from scripts.generacion.perfiles import (
    CAR_MEAN_INCREASE,
    DAILY_HR_SD_BPM,
    DAILY_HRV_LOG_SD,
    DAILY_RR_SD_RPM,
    DAILY_SKIN_TEMP_SD_C,
    DOSE_RATE_MSV_DAY,
    ISS_MEAN_SLEEP_H,
    LUTEAL_SKIN_TEMP_C,
    AstronautProfile,
    MissionConfig,
)
from services.database import Repositorios

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
        return mission_date(self.config.start_date, day)

    def at(self, day: int, at: HourMinute) -> datetime:
        return mission_moment(self.config.start_date, day, at)

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
    SLEEP_START_BLOCK = SLEEP_WINDOW.start_hour * 4
    SLEEP_WINDOW_BLOCKS = SLEEP_WINDOW.hours * 4
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
            start = ctx.at(day, (0, 0))
            for block in range(self.BLOCKS_PER_DAY):
                moment = start + timedelta(minutes=15 * block)
                stage = sleep_stages.get((block - self.SLEEP_START_BLOCK) % self.BLOCKS_PER_DAY, 0)
                in_window = SLEEP_WINDOW.contains(moment.hour)
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
        exercise_day = weekday(day) != REST_WEEKDAY
        if exercise_day and hour == AEROBIC_EXERCISE_HOUR:
            return 2                                             # Aerobic (cycle/treadmill)
        if exercise_day and hour == RESISTANCE_EXERCISE_HOUR:
            return 3                                             # Resistance
        return 1 if ctx.rng.random() < 0.7 else 0

    def _row(self, ctx: AstronautContext, day: int, moment: datetime, stage: int, asleep: bool,
             intensity: int, mods: Modifiers, luteal: float, daily: DailyOffsets) -> dict[str, Any]:
        p = ctx.profile
        hr_delta, hrv_factor, rr_delta, enmo_mean = self.ACTIVITY[intensity]
        if asleep:
            hr_delta, hrv_factor, rr_delta, enmo_mean = -6, 1.2, -1.5, 2.0

        w = mods.watch
        hr = ctx.gauss(p.resting_hr_bpm + hr_delta + w.hr_bpm + daily.hr, 2.5, 40, 190)
        rmssd = (p.rmssd_ms * hrv_factor * w.hrv_factor * daily.hrv_factor
                 * math.exp(ctx.rng.gauss(0, 0.12)))
        systolic = ctx.gauss(114 + 7 * intensity - (6 if asleep else 0), 4, 90, 200)
        diastolic = ctx.gauss(73 + 2.5 * intensity - (4 if asleep else 0), 3, 50, 125)
        skin_base = 33.6 + (0.8 if asleep else 0.0)
        skin = ctx.gauss(skin_base + luteal + w.temp_c + daily.temp, 0.15)
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
            "spo2_pct": round(ctx.gauss(97.8 + w.spo2_pct, 0.6, 85, 100), 1),
            "perfusion_index_pct": round(ctx.gauss(3.0, 0.8, 0.5, 10), 2),
            "respiratory_rate_rpm": round(ctx.gauss(14 + rr_delta + w.rr_rpm + daily.rr, 1.0, 8, 40), 1),
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
    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day in ctx.days():
            mods = ctx.scenario.modifiers(day)
            if weekday(day) in GLUCOSE_WEEKDAYS or mods.hyperglycemia:
                rows.append(self._glucose(ctx, day, GLUCOSE_FASTING_AT, "fasting", ctx.gauss(88, 6, 65, 125)))
                for i, moment in enumerate(GLUCOSE_POST_MEAL_AT):
                    spike = mods.hyperglycemia and i == 1
                    value = ctx.gauss(195, 6) if spike else ctx.gauss(124, 14, 80, 175)
                    rows.append(self._glucose(ctx, day, moment, "post_meal_2h", value))
            if weekday(day) == HEMOGLOBIN_WEEKDAY:
                hb = ctx.profile.baseline_hb_g_dl - 0.02 * day + ctx.rng.gauss(0, 0.2)
                rows.append({
                    "id_mission_log": ctx.id_mission_log, "mission_day": day,
                    "recorded_at": ctx.at(day, HEMOGLOBIN_AT), "hemoglobin_g_dl": round(hb, 2),
                    "measurement_context": "fasting",
                })
        return rows

    @staticmethod
    def _glucose(ctx: AstronautContext, day: int, hm: tuple[int, int], context: str,
                 value: float) -> dict[str, Any]:
        return {
            "id_mission_log": ctx.id_mission_log, "mission_day": day,
            "recorded_at": ctx.at(day, hm), "blood_glucose_mg_dl": round(value, 1),
            "measurement_context": context,
        }


class SalivaGenerator(MeasurementGenerator):
    """One day per week: 4 samples with the normal diurnal cortisol curve."""

    repo = "saliva"
    def generate(self, ctx: AstronautContext) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day in ctx.days():
            if weekday(day) != SALIVA_WEEKDAY:
                continue
            mods = ctx.scenario.modifiers(day)
            wake = ctx.gauss(13.0, 1.8, 8, 20)
            if mods.blunted_car:
                car_factor = ctx.gauss(BLUNTED_CAR_RATIO, 0.04)
            else:
                car_factor = ctx.gauss(1 + CAR_MEAN_INCREASE, 0.12, 1.2)
            if mods.high_night_cortisol:
                night = ctx.gauss(HIGH_NIGHT_CORTISOL_NMOL_L, 0.5)
            else:
                night = ctx.gauss(2.4, 0.6, 0.8, 3.9)
            samples = (
                ("wake_0m", wake, 300.0, 45.0),
                ("wake_30m", wake * car_factor, 310.0, 55.0),
                ("afternoon", ctx.gauss(5.5, 1.0, 3.0, 8.0), 220.0, 110.0),
                ("bedtime", night, 180.0, 90.0),
            )
            for slot, cortisol, dhea_pg, saa_mean in samples:
                dhea_pg_ml = ctx.gauss(dhea_pg, dhea_pg * 0.12, 40)
                rows.append({
                    "id_mission_log": ctx.id_mission_log, "mission_day": day,
                    "recorded_at": ctx.at(day, SALIVA_AT[slot]), "collection_slot": slot,
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
            if weekday(day) != CHAIR_WEEKDAY:
                continue
            mods = ctx.scenario.modifiers(day)
            weight = p.weight_kg * (1 - mods.weight_loss_per_day * day) + ctx.rng.gauss(0, 0.2)
            rows.append({
                "id_mission_log": ctx.id_mission_log, "mission_day": day,
                "recorded_at": ctx.at(day, CHAIR_AT), "weight_kg": round(weight, 2),
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
                "recorded_at": ctx.at(day, (23, 59)), "daily_dose_msv": round(dose, 4),
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


