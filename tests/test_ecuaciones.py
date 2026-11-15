"""Unit test suite for clinical equations (`core/ecuaciones.py`) and readiness (`core/puntaje.py`).

Validates all 17 mathematical models against known reference values from peer-reviewed
literature (Bergenstal 2018, Tanaka 2001, Mifflin 1990, GLIM 2019, Janssen 2004,
EWGSOP2 2019, Wüst 2000, Pruessner 2003, Task Force 1996, Karjalainen 1986,
Battelino 2019, van Hees 2013, Tomić Mahečić 2022) and team custom designs.
"""

from __future__ import annotations

import math
import pytest

from core.ecuaciones import (
    DHEA_PG_ML_TO_NMOL_L,
    compute_anemia_suspicion_index,
    compute_asmi,
    compute_aucg,
    compute_bmi,
    compute_bmr_mifflin,
    compute_car,
    compute_cortisol_dhea_molar_ratio,
    compute_cortisol_diurnal_slope,
    compute_cumulative_radiation_msv,
    compute_delta_mass_pct,
    compute_enmo_mg,
    compute_glim_malnutrition_risk,
    compute_glucose_cv_pct,
    compute_glucose_tir_pct,
    compute_gmi_pct,
    compute_hrv_log_z_score,
    compute_max_hr_tanaka,
    compute_oxygen_delivery_do2,
    compute_readiness_score,
    compute_rmssd,
    compute_sdnn,
    compute_skeletal_muscle_mass_kg,
    compute_skin_temp_delta,
    compute_smi,
    compute_sns_hpa_asymmetry_index,
    compute_z_score,
    convert_dhea_pg_ml_to_nmol_l,
    evaluate_liebermeister_rule,
)
from core.puntaje import (
    build_z_penalties,
    calculate_readiness_from_report,
    evaluate_daily_readiness,
)


# =====================================================================
# 1. BASELINE & STATISTICAL EQUATIONS (Mishra 2020, Task Force 1996)
# =====================================================================

def test_compute_z_score() -> None:
    """Mishra 2020: Z = (x - mu) / sigma, with safe zero-division protection."""
    assert compute_z_score(75.0, 65.0, 5.0) == pytest.approx(2.0)
    assert compute_z_score(60.0, 65.0, 5.0) == pytest.approx(-1.0)
    assert compute_z_score(75.0, 65.0, 0.0) == 0.0
    assert compute_z_score(75.0, 65.0, -2.0) == 0.0


def test_compute_hrv_log_z_score() -> None:
    """Task Force 1996: Log-transformed RMSSD Z-score against personal baseline."""
    baseline = [math.exp(3.5), math.exp(3.7), math.exp(3.9)]  # ln: 3.5, 3.7, 3.9 (mu=3.7, sd=0.2)
    assert compute_hrv_log_z_score(math.exp(3.3), baseline) == pytest.approx(-2.0, rel=1e-5)
    assert compute_hrv_log_z_score(0.0, baseline) == 0.0
    assert compute_hrv_log_z_score(40.0, [42.0]) == 0.0


# =====================================================================
# 2. PROBLEM C: BODY COMPOSITION & METABOLISM (GLIM, Janssen, Mifflin)
# =====================================================================

def test_compute_delta_mass_pct() -> None:
    """GLIM 2019: ((M0 - M_actual) / M0) * 100 where positive means weight loss."""
    assert compute_delta_mass_pct(80.0, 74.0) == pytest.approx(7.5)
    assert compute_delta_mass_pct(80.0, 82.0) == pytest.approx(-2.5)
    with pytest.raises(ValueError, match="initial_weight_kg"):
        compute_delta_mass_pct(0.0, 75.0)


def test_compute_bmi_smi_and_asmi() -> None:
    """GLIM 2019, Janssen 2004, EWGSOP2 2019: BMI, SMM, SMI, and ASMI."""
    assert compute_bmi(80.0, 200.0) == pytest.approx(20.0)
    assert compute_skeletal_muscle_mass_kg(80.0, 40.0) == pytest.approx(32.0)
    assert compute_smi(80.0, 40.0, 200.0) == pytest.approx(8.0)
    assert compute_asmi(24.0, 200.0) == pytest.approx(6.0)


def test_compute_bmr_mifflin() -> None:
    """Mifflin et al. 1990 (AJCN): Resting energy expenditure for males (+5) and females (-161)."""
    assert compute_bmr_mifflin(80.0, 180.0, 35, sex=1) == pytest.approx(1755.0)
    assert compute_bmr_mifflin(60.0, 165.0, 30, sex=0) == pytest.approx(1320.25)


def test_compute_glim_malnutrition_risk() -> None:
    """Cederholm et al. 2019: GLIM Stage 0 (Normal), Stage 1 (Moderate), Stage 2 (Severe)."""
    assert compute_glim_malnutrition_risk(3.0, 22.5, mission_months=4.0) == 0
    assert compute_glim_malnutrition_risk(6.5, 22.5, mission_months=4.0) == 1
    assert compute_glim_malnutrition_risk(2.0, 19.2, mission_months=4.0) == 1
    assert compute_glim_malnutrition_risk(11.0, 21.0, mission_months=4.0) == 2
    assert compute_glim_malnutrition_risk(2.0, 18.1, mission_months=4.0) == 2


# =====================================================================
# 3. PROBLEM F: CIRCADIAN RHYTHM & HPA AXIS (Wüst 2000, Pruessner 2003)
# =====================================================================

def test_cortisol_car_slope_aucg_and_ratios() -> None:
    """Wüst 2000, Pruessner 2003, Mehta 2017 & Ali 2012: CAR, slope, AUCg, C/DHEA, and AOCg."""
    # CAR: 15.0 -> 22.5 nmol/L (+7.5 nmol/L, +50%)
    assert compute_car(15.0, 22.5) == pytest.approx((7.5, 50.0))
    # Diurnal slope: (2.0 - 22.0) / 16 h = -1.25 nmol/L/h
    assert compute_cortisol_diurnal_slope(22.0, 2.0, 16.0) == pytest.approx(-1.25)
    # Trapezoidal AUCg: 9.0 + 112.0 + 28.0 = 149.0
    assert compute_aucg([14.0, 22.0, 6.0, 2.0], [0.0, 0.5, 8.5, 15.5]) == pytest.approx(149.0)
    # DHEA conversion (MW=288.42), C/DHEA molar ratio, and SNS/HPA asymmetry
    assert convert_dhea_pg_ml_to_nmol_l(1000.0) == pytest.approx(1000.0 * DHEA_PG_ML_TO_NMOL_L)
    assert compute_cortisol_dhea_molar_ratio(20.0, dhea_nmol_l=4.0) == pytest.approx(5.0)
    assert compute_sns_hpa_asymmetry_index(300.0, 150.0) == pytest.approx(2.0)


# =====================================================================
# 4. PROBLEM G: HRV & INFECTION (Task Force 1996, Karjalainen 1986)
# =====================================================================

def test_hrv_and_liebermeister_rule() -> None:
    """Task Force 1996 & Karjalainen 1986: RMSSD, SDNN, skin temp delta, and +8.5 bpm/°C rule."""
    rr_ms = [800.0, 830.0, 790.0, 820.0]
    assert compute_rmssd(rr_ms) == pytest.approx(math.sqrt(3400.0 / 3.0))
    assert compute_sdnn(rr_ms) == pytest.approx(math.sqrt(1000.0 / 3.0))
    assert compute_skin_temp_delta(37.2, 36.2) == pytest.approx(1.0)

    # Baseline HR = 60 bpm, +1.0 °C -> expected HR = 68.5 bpm (70 bpm is within 5 bpm tolerance)
    assert evaluate_liebermeister_rule(70.0, 60.0, 1.0, tolerance_bpm=5.0) == pytest.approx((68.5, 0))
    # 82 bpm exceeds 68.5 + 5.0 -> dissociation flag = 1
    assert evaluate_liebermeister_rule(82.0, 60.0, 1.0, tolerance_bpm=5.0) == pytest.approx((68.5, 1))


# =====================================================================
# 5. GLYCEMIC, CARDIO, ENMO, DO2 & RADIATION (Bergenstal, Tanaka, etc.)
# =====================================================================

def test_gmi_cv_and_tir_known_reference_values() -> None:
    """Bergenstal 2018 & Battelino 2019: GMI (154 mg/dL -> 7.0%), CV% (<=36%), and TIR (70-180)."""
    assert compute_gmi_pct(154.0) == pytest.approx(6.99368, rel=1e-5)
    assert round(compute_gmi_pct(154.0), 1) == 7.0
    assert round(compute_gmi_pct(126.0), 1) == 6.3
    assert round(compute_gmi_pct(200.0), 1) == 8.1

    assert compute_glucose_tir_pct([65.0, 100.0, 120.0, 140.0, 195.0]) == pytest.approx(60.0)
    assert compute_glucose_cv_pct([64.0, 100.0, 136.0]) == pytest.approx(36.0)


def test_tanaka_enmo_do2_radiation_and_anemia() -> None:
    """Tanaka 2001, van Hees 2013, Tomić Mahečić 2022 & Zhang 2020."""
    assert compute_max_hr_tanaka(40) == pytest.approx(180.0)
    assert compute_enmo_mg(0.0, 0.0, 1.0) == pytest.approx(0.0)
    assert compute_enmo_mg(0.2, 0.2, 0.2) == pytest.approx(0.0)
    assert compute_enmo_mg(0.0, 0.75, 1.0) == pytest.approx(250.0)

    # CO = 4.2 L/min, CaO2 = 20.1 mL/dL -> DO2 = 844.2 mL/min
    assert compute_oxygen_delivery_do2(60.0, 15.0, 100.0, stroke_volume_ml=70.0) == pytest.approx(844.2)
    assert compute_cumulative_radiation_msv([0.67, 0.67, 1.36]) == pytest.approx(2.70)
    assert compute_anemia_suspicion_index(2.0, 1.5, 1.0, 15.0, 13.2) == (pytest.approx(1.65), 1)


# =====================================================================
# 6. INTEGRATION WITH `core/puntaje.py` (Directional Penalties & Score)
# =====================================================================

def test_readiness_penalties_and_score() -> None:
    """Verifies that `core/puntaje.py` orients all 7 metrics so positive = worse."""
    # Case 1: Optimal astronaut -> 0.0 across all penalties -> score = 100.0
    optimal = build_z_penalties(
        hrv_z_score=1.2, resting_hr_z=-0.8, sleep_total_min=480,
        skin_temp_delta_c=-0.1, aucg_cortisol_z=-0.5, cortisol_dhea_ratio_z=0.0,
        delta_mass_pct=-1.0, glucose_cv_pct=28.0,
    )
    assert all(v == 0.0 for v in optimal.values())
    assert compute_readiness_score(optimal) == 100.0

    # Case 2: Degraded report -> 8(1.2)+7(2.0)+6(1.5)+7(0.5)+5(1.8)+5(1.0)+4(2.0) = 58.1 -> 41.9
    report = {
        "sleep_total_min": 348, "hrv_z_score": -2.0, "resting_hr_z": 1.5,
        "skin_temp_delta_c": 0.5, "aucg_cortisol_z": 1.8,
        "delta_mass_pct": 1.0, "glucose_cv_pct": 38.0,
    }
    breakdown = evaluate_daily_readiness(report)
    expected_penalties = {
        "sleep_deficit": 1.2, "hrv_drop": 2.0, "resting_hr_elevation": 1.5,
        "skin_temp_deviation": 0.5, "cortisol_imbalance": 1.8,
        "muscle_loss": 1.0, "glycemic_instability": 2.0,
    }
    assert breakdown.z_penalties == pytest.approx(expected_penalties)
    assert breakdown.readiness_score == pytest.approx(41.9)
    assert calculate_readiness_from_report(report) == pytest.approx(41.9)
