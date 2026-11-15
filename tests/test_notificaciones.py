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
    path = tmp_path_factory.mktemp("notif") / "mission.db"
    generate_mission(MissionConfig(days=16, seed=5), path, sync_nasa=False)
    process_mission_log(1, db_path=path, bus=EventBus())
    return path


@pytest.fixture
def db_path(template: Path, tmp_path: Path) -> Path:
    path = tmp_path / "copy.db"
    shutil.copy(template, path)
    return path


def test_channels_registry() -> None:
    assert set(CHANNELS) == {"email", "log"}


def test_observer_dispatches_spanish_email_and_marks_email_sent(db_path: Path) -> None:
    bus, outbox = EventBus(), []

    def fake_sender(recipients: list[str], subject: str, body: str) -> bool:
        outbox.append((recipients, subject, body))
        return True

    subscribe_notifications(bus, db_path=db_path, sender=fake_sender)
    process_days(1, [15], db_path=db_path, bus=bus)  # Day 15 triggers S1 on astronaut 1

    assert len(outbox) == 1
    recipients, subject, body = outbox[0]
    assert "andrea.solis@spaceoddity.dev" in recipients
    assert "laura.jimenez@spaceoddity.dev" in recipients
    assert "[Astro-Health] Alerta" in subject and "Plan de acción:" in body

    with sesion(db_path) as db:
        alerts = [a for a in db.alertas.listar(id_mission_log=1) if a["mission_day"] == 15]
    assert alerts and all(a["email_sent"] == 1 for a in alerts)


def test_sending_same_alert_twice_is_idempotent(db_path: Path) -> None:
    bus, events, outbox = EventBus(), [], []
    bus.subscribe(events.append)
    process_days(1, [15], db_path=db_path, bus=bus)

    sender = lambda r, s, b: outbox.append((r, s, b)) or True
    assert send_alert_notification(events[0], db_path=db_path, sender=sender) is True
    assert send_alert_notification(events[0], db_path=db_path, sender=sender) is False
    assert len(outbox) == 1
