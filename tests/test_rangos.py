"""Boundary tests for core/rangos.py: each threshold, just below and at the limit."""

from __future__ import annotations

import pytest

from core.rangos import (
    THRESHOLDS,
    evaluate_overall_health,
    evaluate_problem_c,
    evaluate_problem_f,
    evaluate_problem_g,
    evaluate_safety,
)


def _sev(rules: list, metric: str) -> str | None:
    return next((r.severity for r in rules if r.metric == metric), None)


@pytest.mark.parametrize(
    ("kwargs", "metric", "expected"),
    [
        ({"delta_mass_pct": 4.99}, "delta_mass_pct", None),
        ({"delta_mass_pct": 5.0}, "delta_mass_pct", "yellow"),        # >= 5 % (GLIM stage 1)
        ({"delta_mass_pct": 10.0}, "delta_mass_pct", "yellow"),
        ({"delta_mass_pct": 10.01}, "delta_mass_pct", "red"),         # > 10 % (GLIM stage 2)
        ({"bmi": 20.0}, "bmi", None),
        ({"bmi": 19.99}, "bmi", "yellow"),
        ({"bmi": 18.49}, "bmi", "red"),
        ({"smi_total_kg_m2": 10.75, "sex": 1}, "smi_total_kg_m2", "yellow"),   # <= is inclusive
        ({"smi_total_kg_m2": 8.5, "sex": 1}, "smi_total_kg_m2", "red"),
        ({"smi_total_kg_m2": 6.76, "sex": 0}, "smi_total_kg_m2", None),
        ({"visceral_fat_level": 13}, "visceral_fat_level", "yellow"),
        ({"visceral_fat_level": 13, "smi_total_kg_m2": 8.0}, "visceral_fat_level", "red"),
    ],
)
def test_problem_c_thresholds(kwargs: dict, metric: str, expected: str | None) -> None:
    assert _sev(evaluate_problem_c(**kwargs), metric) == expected


def test_problem_f_sleep_streaks_and_cortisol() -> None:
    assert _sev(evaluate_problem_f([5.9, 5.9, 5.9]), "sleep_total_min") == "yellow"   # 3 nights < 6 h
    assert _sev(evaluate_problem_f([5.9, 5.9]), "sleep_total_min") is None
    assert _sev(evaluate_problem_f([4.9, 4.9]), "sleep_total_min") == "red"           # 2 nights < 5 h
    assert _sev(evaluate_problem_f(car_absolute_nmol_l=2.4, car_blunted_consecutive_weeks=2),
                "car_absolute_nmol_l") == "red"
    assert _sev(evaluate_problem_f(cortisol_morning_nmol_l=24.0), "cortisol_07_08h") is None
    assert _sev(evaluate_problem_f(cortisol_morning_nmol_l=24.1), "cortisol_07_08h") == "yellow"
    night = evaluate_problem_f(cortisol_night_nmol_l=4.1, cortisol_dhea_ratio_z=1.6)
    assert _sev(night, "cortisol_23_24h") == "red"                                    # + rising ratio


def test_problem_g_needs_temperature_and_a_second_sign() -> None:
    fever = [0.6, 0.6, 0.6]
    assert evaluate_problem_g(skin_temp_deltas_history=fever) == []                   # Temperature alone
    assert evaluate_problem_g(resting_hr_bpm=95) == []                                # Sign alone
    rules = evaluate_problem_g(skin_temp_deltas_history=fever, resting_hr_bpm=95)
    assert _sev(rules, "skin_temp_delta_c") == "red" and _sev(rules, "resting_hr_bpm") == "red"


def test_problem_g_luteal_compensation_raises_the_limit() -> None:
    kwargs = {"skin_temp_deltas_history": [0.6, 0.6], "hrv_z_score": -3.0, "sex": 0,
              "cycle_phase": "luteal"}
    assert evaluate_problem_g(**kwargs)                                               # Not compensated
    # 0.6 °C is below the compensated limit (0.5 + 0.4)
    assert evaluate_problem_g(**kwargs, compensate_luteal_in_code=True) == []


@pytest.mark.parametrize(
    ("glucose", "expected"),
    [(53.9, "red"), (54.0, "yellow"), (70.0, None), (180.1, "yellow"), (250.1, "red")],
)
def test_safety_glucose(glucose: float, expected: str | None) -> None:
    assert _sev(evaluate_safety(blood_glucose_mg_dl=glucose), "blood_glucose_mg_dl") == expected


def test_safety_pressure_spo2_and_radiation() -> None:
    assert _sev(evaluate_safety(systolic_bp_mmhg=180, diastolic_bp_mmhg=80), "blood_pressure_mmhg") == "red"
    stage_2 = evaluate_safety(systolic_bp_mmhg=139, diastolic_bp_mmhg=90)
    assert _sev(stage_2, "blood_pressure_mmhg") == "yellow"
    assert _sev(evaluate_safety(spo2_pct=94.9), "spo2_pct") == "yellow"
    assert _sev(evaluate_safety(spo2_pct=97.0, crew_spo2_drop_count=2), "spo2_pct") == "red"
    storm = evaluate_safety(proton_flux_pfu=THRESHOLDS["radiation"]["proton_pfu"])
    assert _sev(storm, "radiation_spe") == "red"
    assert _sev(evaluate_safety(cumulative_radiation_msv=150.0), "cumulative_radiation_msv") == "yellow"


def test_overall_triage_and_sampling_priority() -> None:
    result = evaluate_overall_health(
        evaluate_problem_c(delta_mass_pct=6.0), evaluate_problem_f([4.0, 4.0]), [], []
    )
    assert result.triage_status == "red"
    assert result.recommended_sampling_mode == "alert_atrophy"      # C has priority over F
    assert result.category_severities == {"C": "yellow", "F": "red", "G": "normal", "S": "normal"}
