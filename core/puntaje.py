"""Daily Operational Readiness Score builder and penalty normalization module.

Transforms daily physiological metrics (from `daily_health_reports` or raw inputs)
into directional, non-negative penalty magnitudes (`z_penalties`) where a positive
value strictly represents physiological degradation, then delegates the weighted
calculation to `compute_readiness_score` in `core.ecuaciones`.

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping

try:
    from core.ecuaciones import compute_readiness_score
except ImportError:
    from core.equations import compute_readiness_score  # Fallback if renamed


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

    def to_dict(self) -> dict[str, Any]:
        """Serializes the breakdown into a plain dictionary."""
        return asdict(self)


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

    Args:
        hrv_z_score: Log-transformed RMSSD Z-score vs. 28-day baseline.
        resting_hr_z: Resting heart rate Z-score vs. personal baseline.
        sleep_total_min: Total sleep duration in minutes (from `daily_health_reports`).
        sleep_hours: Total sleep duration in hours (used if `sleep_total_min` is None).
        skin_temp_delta_c: Nocturnal skin temperature deviation from 28-day baseline (°C).
        aucg_cortisol_z: Total daily cortisol AUCg Z-score.
        cortisol_dhea_ratio_z: Cortisol/DHEA molar ratio Z-score.
        delta_mass_pct: Body mass loss percentage `((M0 - M) / M0) * 100`.
        glucose_cv_pct: Glycemic coefficient of variation percentage (`CV%`).
        scale_non_z_metrics: If True, normalizes `skin_temp_delta_c`, `delta_mass_pct`,
            and `glucose_cv_pct` by clinical step thresholds so they match Z-score units.

    Returns:
        Dictionary of 7 directional penalty values (>= 0.0) ready for `compute_readiness_score`.
    """
    # 1. HRV drop: invert sign because negative Z-score indicates autonomic suppression
    hrv_drop = max(0.0, -float(hrv_z_score)) if hrv_z_score is not None else 0.0

    # 2. Resting HR elevation: positive Z-score indicates tachycardia/stress
    hr_elevation = max(0.0, float(resting_hr_z)) if resting_hr_z is not None else 0.0

    # 3. Sleep deficit: hours below the 7.0 h AASM/SRS minimum (sleeping > 7 h gives 0 penalty)
    resolved_sleep_hours: float | None = None
    if sleep_total_min is not None:
        resolved_sleep_hours = float(sleep_total_min) / 60.0
    elif sleep_hours is not None:
        resolved_sleep_hours = float(sleep_hours)

    sleep_deficit = (
        max(0.0, TARGET_SLEEP_HOURS - resolved_sleep_hours)
        if resolved_sleep_hours is not None
        else 0.0
    )

    # 4. Nocturnal skin temperature deviation: positive fever/inflammatory shift
    raw_temp_dev = max(0.0, float(skin_temp_delta_c)) if skin_temp_delta_c is not None else 0.0
    skin_temp_deviation = (
        raw_temp_dev / TEMP_DELTA_SCALE_C if scale_non_z_metrics else raw_temp_dev
    )

    # 5. Cortisol / HPA axis imbalance: highest positive Z-score between AUCg and C/DHEA ratio
    cort_candidates = [
        float(val)
        for val in (aucg_cortisol_z, cortisol_dhea_ratio_z)
        if val is not None
    ]
    cortisol_imbalance = max([0.0, *cort_candidates])

    # 6. Muscle / body mass loss: positive delta_mass_pct indicates mass loss
    raw_mass_loss = max(0.0, float(delta_mass_pct)) if delta_mass_pct is not None else 0.0
    muscle_loss = (
        raw_mass_loss / MUSCLE_LOSS_SCALE_PCT if scale_non_z_metrics else raw_mass_loss
    )

    # 7. Glycemic instability: excess CV% above the 36.0% international consensus target
    raw_cv_excess = (
        max(0.0, float(glucose_cv_pct) - GLYCEMIC_CV_THRESHOLD_PCT)
        if glucose_cv_pct is not None
        else 0.0
    )
    glycemic_instability = (
        raw_cv_excess / GLYCEMIC_CV_SCALE_PCT if scale_non_z_metrics else raw_cv_excess
    )

    return {
        "sleep_deficit": round(sleep_deficit, 4),
        "hrv_drop": round(hrv_drop, 4),
        "resting_hr_elevation": round(hr_elevation, 4),
        "skin_temp_deviation": round(skin_temp_deviation, 4),
        "cortisol_imbalance": round(cortisol_imbalance, 4),
        "muscle_loss": round(muscle_loss, 4),
        "glycemic_instability": round(glycemic_instability, 4),
    }


# =====================================================================
# 3. HIGH-LEVEL ENTRYPOINTS FOR SERVICES & PIPELINES
# =====================================================================

def evaluate_daily_readiness(
    daily_report: Mapping[str, Any] | None = None,
    *,
    weights: dict[str, float] | None = None,
    scale_non_z_metrics: bool = False,
    **metric_overrides: Any,
) -> ReadinessBreakdown:
    """Computes the daily readiness score (0-100) and penalty breakdown from a report row.

    Accepts a dictionary/row matching `daily_health_reports` column names, plus optional
    keyword overrides (e.g., `resting_hr_z`, `aucg_cortisol_z`, `cortisol_dhea_ratio_z`).
    """
    data: dict[str, Any] = dict(daily_report) if daily_report is not None else {}
    data.update(metric_overrides)

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

    score = compute_readiness_score(z_penalties=z_penalties, weights=weights)
    return ReadinessBreakdown(readiness_score=score, z_penalties=z_penalties)


def calculate_readiness_from_report(
    daily_report: Mapping[str, Any],
    weights: dict[str, float] | None = None,
) -> float:
    """Convenience wrapper returning only the float `readiness_score` (0-100) for DB storage."""
    return evaluate_daily_readiness(daily_report, weights=weights).readiness_score


if __name__ == "__main__":
    import json

    # Example simulated row from `daily_health_reports`
    sample_report = {
        "sleep_total_min": 348,       # 5.8 hours -> 1.2 h sleep deficit
        "hrv_z_score": -2.0,          # Inverted to +2.0 hrv_drop
        "resting_hr_z": 1.4,          # Kept as +1.4 resting_hr_elevation
        "skin_temp_delta_c": 0.6,     # +0.6 °C above baseline
        "cortisol_dhea_ratio_z": 1.8, # +1.8 Z-score HPA imbalance
        "delta_mass_pct": 2.5,        # 2.5% body mass lost
        "glucose_cv_pct": 38.5,       # 2.5% above the 36% CV threshold
    }

    result = evaluate_daily_readiness(sample_report)
    print(json.dumps(result.to_dict(), indent=2))
