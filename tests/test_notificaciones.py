"""Tests for services/notificaciones.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.database import sesion
from services.eventos import EventBus
from services.notificaciones import CHANNELS, send_alert_notification, subscribe_notifications
from services.reportes import process_days


@pytest.fixture
def db_path(mission_copy) -> Path:
    """16-day mission (day 15 triggers S1 hyperglycemia on astronaut 1)."""
    return mission_copy(16, 5)


def test_channels_registry() -> None:
    """Verifies that delivery channel strategies are registered in `CHANNELS`."""
    assert set(CHANNELS) == {"email", "log"}


def test_observer_sends_spanish_email_once_and_marks_db(db_path: Path) -> None:
    """Subscribes to EventBus, runs day 15, and verifies Spanish email and `email_sent = 1`."""
    bus, outbox = EventBus(), []
    # Register observer with an in-memory sender spy
    subscribe_notifications(bus, db_path=db_path, sender=lambda r, s, b: outbox.append((r, s, b)) or True)
    process_days(1, [15], db_path=db_path, bus=bus)

    # Verify email was dispatched to both the astronaut and the flight surgeon in Spanish
    assert len(outbox) == 1
    recipients, subject, body = outbox[0]
    assert {"andrea.solis@spaceoddity.dev", "laura.jimenez@spaceoddity.dev"} <= set(recipients)
    assert "[Astro-Health] Alert" in subject and "Action plan:" in body

    # Verify `email_sent = 1` was persisted in the alerts table
    with sesion(db_path) as db:
        alerts = [a for a in db.alertas.listar(id_mission_log=1) if a["mission_day"] == 15]
    assert alerts and all(a["email_sent"] == 1 for a in alerts)


def test_send_alert_notification_is_idempotent(db_path: Path) -> None:
    """Verifies that processing the same AlertEvent twice sends only one email."""
    bus, events, outbox = EventBus(), [], []
    bus.subscribe(events.append)
    process_days(1, [15], db_path=db_path, bus=bus)

    def sender(recipients: list[str], subject: str, body: str) -> bool:
        outbox.append((recipients, subject, body))
        return True

    # First call dispatches email and sets email_sent = 1; second call is a no-op
    assert send_alert_notification(events[0], db_path=db_path, sender=sender) is True
    assert send_alert_notification(events[0], db_path=db_path, sender=sender) is False
    assert len(outbox) == 1
