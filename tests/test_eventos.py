"""Tests for services/eventos.py (Observer)."""

from __future__ import annotations

from core.alertas import build_alerts
from core.rangos import evaluate_overall_health, evaluate_safety
from services.eventos import AlertEvent, EventBus


def _event() -> AlertEvent:
    evaluation = evaluate_overall_health([], [], [], evaluate_safety(blood_glucose_mg_dl=48.0))
    (draft,) = build_alerts(evaluation, mission_day=3)
    return AlertEvent(alert_id=1, id_mission_log=1, astronaut={"email": "a@b.c"}, alert=draft)


def test_every_subscriber_receives_the_event() -> None:
    bus, seen = EventBus(), []
    bus.subscribe(lambda e: seen.append(("email", e.alert_id)))
    bus.subscribe(lambda e: seen.append(("ui", e.alert_id)))
    assert bus.publish(_event()) == 2
    assert seen == [("email", 1), ("ui", 1)]


def test_failing_subscriber_does_not_stop_the_others() -> None:
    bus, seen = EventBus(), []

    def broken(event: AlertEvent) -> None:
        raise RuntimeError("SMTP down")

    bus.subscribe(broken)
    bus.subscribe(lambda e: seen.append(e.alert_id))
    assert bus.publish(_event()) == 1
    assert seen == [1]


def test_unsubscribe_and_no_duplicates() -> None:
    bus, seen = EventBus(), []

    def handler(event: AlertEvent) -> None:
        seen.append(event.alert_id)

    unsubscribe = bus.subscribe(handler)
    bus.subscribe(handler)                       # Registering twice has no effect
    bus.publish(_event())
    unsubscribe()
    bus.publish(_event())
    assert seen == [1]


def test_red_alert_is_immediate() -> None:
    assert _event().is_immediate
