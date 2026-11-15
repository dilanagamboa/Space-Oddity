"""Physiology of the clinical scenarios, defined once.

Used by the data generator (demo cases baked into the mission) and by the doctor's
simulator (scenarios applied on top of real data), so a fever, a hypertensive crisis
or a solar storm look the same wherever they appear.

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

FEVER_HR_BPM_PER_C: float = 8.5          # Karjalainen & Viitasalo 1986
SPE_DOSE_FACTOR: float = 2.5             # Team assumption: dose on a solar particle event day
BLUNTED_CAR_RATIO: float = 1.12          # Cortisol +30 min / at waking when the CAR is flattened
HIGH_NIGHT_CORTISOL_NMOL_L: float = 5.6  # Above the 23-24 h reference (1-4 nmol/L)
SPO2_FLOOR_PCT: float = 85.0


@dataclass(frozen=True)
class WatchChange:
    """Shift applied to the watch signals (cardiovascular_autonomic) of a day."""

    temp_c: float = 0.0
    hr_bpm: float = 0.0
    hrv_factor: float = 1.0
    rr_rpm: float = 0.0
    spo2_pct: float = 0.0
    enmo_factor: float = 1.0
    systolic_mmhg: float = 0.0
    diastolic_mmhg: float = 0.0
    awake_only: bool = False

    def apply(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Returns a copy of a watch row with the change applied."""
        out = dict(row)
        if self.awake_only and out["sleep_stage"] > 0:
            return out
        out["skin_temperature_c"] = round(out["skin_temperature_c"] + self.temp_c, 2)
        out["skin_temp_deviation_c"] = round((out["skin_temp_deviation_c"] or 0.0) + self.temp_c, 2)
        out["heart_rate_bpm"] = round(out["heart_rate_bpm"] + self.hr_bpm, 1)
        out["rr_mean_ms"] = round(60000.0 / out["heart_rate_bpm"], 1)
        out["hrv_rmssd_ms"] = round(out["hrv_rmssd_ms"] * self.hrv_factor, 1)
        out["hrv_sdnn_ms"] = round(out["hrv_sdnn_ms"] * self.hrv_factor, 1)
        out["respiratory_rate_rpm"] = round(out["respiratory_rate_rpm"] + self.rr_rpm, 1)
        out["spo2_pct"] = round(min(100.0, max(out["spo2_pct"] + self.spo2_pct, SPO2_FLOOR_PCT)), 1)
        out["enmo_mg"] = round(out["enmo_mg"] * self.enmo_factor, 1)
        if self.systolic_mmhg or self.diastolic_mmhg:
            systolic = out["estimated_systolic_bp_mmhg"] + self.systolic_mmhg
            diastolic = out["estimated_diastolic_bp_mmhg"] + self.diastolic_mmhg
            out.update({
                "estimated_systolic_bp_mmhg": systolic,
                "estimated_diastolic_bp_mmhg": diastolic,
                "estimated_map_mmhg": round(diastolic + (systolic - diastolic) / 3, 1),
            })
        return out


def infection(fever_c: float) -> WatchChange:
    """Febrile infection: temperature, tachycardia (+8.5 bpm/°C), low HRV, fast breathing."""
    return WatchChange(
        temp_c=fever_c, hr_bpm=FEVER_HR_BPM_PER_C * fever_c + 14, hrv_factor=0.55,
        rr_rpm=7.0, spo2_pct=-1.0,
    )


CIRCADIAN = WatchChange(hr_bpm=4, hrv_factor=0.75)
ANEMIA = WatchChange(hr_bpm=12, spo2_pct=-3.5, enmo_factor=0.6)
HYPERTENSIVE_CRISIS = WatchChange(hr_bpm=10, systolic_mmhg=75, diastolic_mmhg=50, awake_only=True)
