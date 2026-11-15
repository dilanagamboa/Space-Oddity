"""Tests for core/planes.py and core/alertas.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.alertas import (
    ALERT_CATEGORY_BY_NOTIFICATION,
    build_alerts,
    filter_repeated,
)
from core.enums import CategoriaAlerta
from core.planes import ACTION_PLANS, get_action_plan
from core.rangos import (
    evaluate_overall_health,
    evaluate_problem_c,
    evaluate_problem_g,
    evaluate_safety,
)
from services.database import Repositorios

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def _infection_and_glucose():
    """Red infection (G1) + yellow hyperglycemia (S1), like the rangos.py demo."""
    rules_g = evaluate_problem_g(
        skin_temp_deltas_history=[0.6, 0.7, 0.8], core_temp_c=38.3,
        resting_hr_bpm=94.0, hrv_z_score=-2.7, respiratory_rate_rpm=22.0,
    )
    rules_s = evaluate_safety(blood_glucose_mg_dl=195.0)
    return evaluate_overall_health([], [], rules_g, rules_s)


def _recent(notification_id: str, severity: str, hours_ago: float) -> dict:
    return {
        "severity": severity,
        "created_at": (NOW - timedelta(hours=hours_ago)).replace(tzinfo=None).isoformat(),
        "triggered_rules_json": {"notification_id": notification_id},
    }


# --- Plans --------------------------------------------------------------------
def test_every_notification_has_plan_and_category() -> None:
    assert set(ACTION_PLANS) == set(ALERT_CATEGORY_BY_NOTIFICATION)
    valid_categories = {c.value for c in CategoriaAlerta}
    assert set(ALERT_CATEGORY_BY_NOTIFICATION.values()) <= valid_categories


def test_plan_render_is_numbered() -> None:
    text = get_action_plan("S4").render("red")
    assert text.startswith("1. ") and "refugio" in text


def test_red_escalates_recipients() -> None:
    plan = get_action_plan("G1")
    assert "commander" not in plan.recipients("yellow")
    assert {"commander", "crew"} <= set(plan.recipients("red"))


def test_unknown_plan_raises() -> None:
    with pytest.raises(KeyError, match="Z9"):
        get_action_plan("Z9")


# --- Build --------------------------------------------------------------------
def test_build_groups_by_notification_and_orders_red_first() -> None:
    drafts = build_alerts(_infection_and_glucose(), mission_day=10)
    assert [(d.notification_id, d.severity) for d in drafts] == [("G1", "red"), ("S1", "yellow")]
    g1 = drafts[0]
    assert g1.alert_category == "sepsis_infection"
    assert g1.sampling_mode_triggered == "alert_infection"
    assert len(g1.triggered_rules) == 5  # skin temp, core temp, HR, HRV, RR


def test_same_notification_takes_highest_severity() -> None:
    # Mass loss yellow (C1) + BMI red (C1) -> one red C1 alert
    rules_c = evaluate_problem_c(delta_mass_pct=6.0, bmi=18.0)
    drafts = build_alerts(evaluate_overall_health(rules_c, [], [], []), mission_day=3)
    assert len(drafts) == 1
    assert drafts[0].notification_id == "C1" and drafts[0].severity == "red"


def test_healthy_day_has_no_alerts() -> None:
    assert build_alerts(evaluate_overall_health([], [], [], []), mission_day=1) == []


# --- Anti-repetition ----------------------------------------------------------
def test_does_not_repeat_within_24h() -> None:
    drafts = build_alerts(_infection_and_glucose(), mission_day=10)
    kept = filter_repeated(drafts, [_recent("G1", "red", 5)], NOW)
    assert [d.notification_id for d in kept] == ["S1"]


def test_repeats_after_24h() -> None:
    drafts = build_alerts(_infection_and_glucose(), mission_day=10)
    kept = filter_repeated(drafts, [_recent("G1", "red", 25)], NOW)
    assert {d.notification_id for d in kept} == {"G1", "S1"}


def test_escalation_from_yellow_to_red_is_sent() -> None:
    drafts = build_alerts(_infection_and_glucose(), mission_day=10)
    kept = filter_repeated(drafts, [_recent("G1", "yellow", 2)], NOW)
    assert "G1" in {d.notification_id for d in kept}


# --- Urgency ------------------------------------------------------------------
def test_only_red_alerts_are_immediate() -> None:
    drafts = build_alerts(_infection_and_glucose(), mission_day=10)
    assert [(d.notification_id, d.is_immediate) for d in drafts] == [("G1", True), ("S1", False)]


# --- Integration with the database --------------------------------------------
def test_draft_fits_alerts_table(db: Repositorios, escenario: dict) -> None:
    drafts = build_alerts(_infection_and_glucose(), mission_day=10)
    for draft in drafts:
        db.alertas.insertar(**draft.to_db_row(escenario["log"]))

    stored = db.alertas.activas(escenario["log"])
    assert len(stored) == 2
    assert {s["triggered_rules_json"]["notification_id"] for s in stored} == {"G1", "S1"}

    # What was just stored blocks the same alerts in the next evaluation
    assert filter_repeated(drafts, stored, datetime.now(UTC)) == []
