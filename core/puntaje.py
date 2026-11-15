"""Daily Operational Readiness Score builder and penalty normalization module.

Transforms daily physiological metrics (from `daily_health_reports` or raw inputs)
into directional, non-negative penalty magnitudes (`z_penalties`) where a positive
value strictly represents physiological degradation, then delegates the weighted
calculation to `compute_readiness_score` in `core.ecuaciones`.

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.ecuaciones import compute_readiness_score

# =====================================================================
# 1. CLINICAL NORMALIZATION CONSTANTS
# =====================================================================

TARGET_SLEEP_HOURS: float = 7.0          # AASM/SRS consensus minimum [Watson 2015]
GLYCEMIC_CV_THRESHOLD_PCT: float = 36.0  # International TIR consensus [Battelino 2019]

# Scaling divisors to bring percentage/Celsius metrics onto a Z-score-like (0-3+) scale
TEMP_DELTA_SCALE_C: float = 0.5          # +0.5 °C deviation equals 1.0 penalty unit [Oura]
MUSCLE_LOSS_SCALE_PCT: float = 5.0       # 5% mass loss (GLIM Stage 1) equals 1.0 penalty unit
GLYCEMIC_CV_SCALE_PCT: float = 4.0       # Every +4% above 36% CV equals 1.0 penalty unit


@dataclass(frozen=True)
class ReadinessBreakdown:
    """Immutable value object holding the final score and its penalty components."""

    readiness_score: float
    z_penalties: dict[str, float]



def _pos(value: float | int | None, scale: float = 1.0) -> float:
    """Clamps a metric to >= 0.0, divides by `scale`, and rounds to 4 decimals."""
    return round(max(0.0, float(value)) / scale, 4) if value is not None else 0.0


# =====================================================================
# 2. DIRECTIONAL PENALTY BUILDERS
# =====================================================================

def build_z_penalties(
    *,
    hrv_z_score: float | None = None,
    resting_hr_z: float | None = None,
    sleep_total_min: float | int | None = None,
    sleep_hours: float | None = None,
    skin_temp_delta_c: float | None = None,
    aucg_cortisol_z: float | None = None,
    cortisol_dhea_ratio_z: float | None = None,
    delta_mass_pct: float | None = None,
    glucose_cv_pct: float | None = None,
    scale_non_z_metrics: bool = False,
) -> dict[str, float]:
    """Builds the `z_penalties` dictionary orienting all metrics so positive = worse.

    Directional rules:
    - `hrv_drop`: `-hrv_z_score` (Low HRV is deleterious; sign is inverted).
    - `resting_hr_elevation`: `resting_hr_z` (High resting HR is deleterious; kept as is).
    - `sleep_deficit`: Hours missing to reach 7.0 h (`max(0.0, 7.0 - sleep_hours)`).
    - `skin_temp_deviation`: Positive nocturnal temperature elevation above baseline.
    - `cortisol_imbalance`: Worst positive Z-score between `aucg_cortisol_z` and
      `cortisol_dhea_ratio_z`.
    - `muscle_loss`: Positive body mass loss percentage (`delta_mass_pct`).
    - `glycemic_instability`: Amount by which `glucose_cv_pct` exceeds the 36% threshold.
    """
    # Resolve sleep hours from minutes (`daily_health_reports`) or direct hours
    sleep_h = float(sleep_total_min) / 60.0 if sleep_total_min is not None else sleep_hours
    sleep_gap = (TARGET_SLEEP_HOURS - float(sleep_h)) if sleep_h is not None else None

    # Worst positive HPA axis Z-score between total daily cortisol (AUCg) and C/DHEA ratio
    cortisol_worst = max(
        (float(v) for v in (aucg_cortisol_z, cortisol_dhea_ratio_z) if v is not None),
        default=0.0,
    )

    # Excess glycemic CV% above the 36.0% international consensus target
    cv_excess = (float(glucose_cv_pct) - GLYCEMIC_CV_THRESHOLD_PCT) if glucose_cv_pct is not None else None

    return {
        # 1. Sleep deficit: hours missing to reach the 7.0 h AASM/SRS minimum
        "sleep_deficit": _pos(sleep_gap),
        # 2. HRV drop: invert sign because negative Z-score indicates autonomic suppression
        "hrv_drop": _pos(-float(hrv_z_score) if hrv_z_score is not None else None),
        # 3. Resting HR elevation: positive Z-score indicates tachycardia/stress
        "resting_hr_elevation": _pos(resting_hr_z),
        # 4. Nocturnal skin temperature deviation: positive fever/inflammatory shift
        "skin_temp_deviation": _pos(skin_temp_delta_c, TEMP_DELTA_SCALE_C if scale_non_z_metrics else 1.0),
        # 5. Cortisol / HPA axis imbalance: highest positive Z-score
        "cortisol_imbalance": _pos(cortisol_worst),
        # 6. Muscle / body mass loss: positive delta_mass_pct indicates mass loss
        "muscle_loss": _pos(delta_mass_pct, MUSCLE_LOSS_SCALE_PCT if scale_non_z_metrics else 1.0),
        # 7. Glycemic instability: excess CV% above 36%
        "glycemic_instability": _pos(cv_excess, GLYCEMIC_CV_SCALE_PCT if scale_non_z_metrics else 1.0),
    }


# =====================================================================
# 3. HIGH-LEVEL ENTRYPOINTS FOR SERVICES & PIPELINES
# =====================================================================

def evaluate_daily_readiness(
    daily_report: Mapping[str, Any] | None = None,
    *,
    weights: Mapping[str, float] | None = None,
    scale_non_z_metrics: bool = False,
    **metric_overrides: Any,
) -> ReadinessBreakdown:
    """Computes the daily readiness score (0-100) and penalty breakdown from a report row."""
    data = {**(daily_report or {}), **metric_overrides}
    z_penalties = build_z_penalties(
        hrv_z_score=data.get("hrv_z_score"),
        resting_hr_z=data.get("resting_hr_z"),
        sleep_total_min=data.get("sleep_total_min"),
        sleep_hours=data.get("sleep_hours"),
        skin_temp_delta_c=data.get("skin_temp_delta_c"),
        aucg_cortisol_z=data.get("aucg_cortisol_z"),
        cortisol_dhea_ratio_z=data.get("cortisol_dhea_ratio_z"),
        delta_mass_pct=data.get("delta_mass_pct"),
        glucose_cv_pct=data.get("glucose_cv_pct"),
        scale_non_z_metrics=scale_non_z_metrics,
    )
    return ReadinessBreakdown(compute_readiness_score(z_penalties, weights), z_penalties)
