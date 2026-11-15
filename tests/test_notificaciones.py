"""Tests for services/notificaciones.py."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.generar_datos import MissionConfig, generate_mission
from services.database import sesion
from services.eventos import EventBus
from services.notificaciones import CHANNELS, send_alert_notification, subscribe_notifications
from services.reportes import process_days, process_mission_log


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generates a 16-day mission (day 15 triggers S1 hyperglycemia on astronaut 1)."""
    path = tmp_path_factory.mktemp("notif") / "mission.db"
    generate_mission(MissionConfig(days=16, seed=5), path, sync_nasa=False)
    process_mission_log(1, db_path=path, bus=EventBus())
    return path


@pytest.fixture
def db_path(template: Path, tmp_path: Path) -> Path:
    """Copies the template DB before each test for isolation."""
    path = tmp_path / "copy.db"
    shutil.copy(template, path)
    return path


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
    assert "[Astro-Health] Alerta" in subject and "Plan de acción:" in body

    # Verify `email_sent = 1` was persisted in the alerts table
    with sesion(db_path) as db:
        alerts = [a for a in db.alertas.listar(id_mission_log=1) if a["mission_day"] == 15]
    assert alerts and all(a["email_sent"] == 1 for a in alerts)


def test_send_alert_notification_is_idempotent(db_path: Path) -> None:
    """Verifies that processing the same AlertEvent twice sends only one email."""
    bus, events, outbox = EventBus(), [], []
    bus.subscribe(events.append)
    process_days(1, [15], db_path=db_path, bus=bus)

    sender = lambda r, s, b: outbox.append((r, s, b)) or True
    # First call dispatches email and sets email_sent = 1; second call is a no-op
    assert send_alert_notification(events[0], db_path=db_path, sender=sender) is True
    assert send_alert_notification(events[0], db_path=db_path, sender=sender) is False
    assert len(outbox) == 1
