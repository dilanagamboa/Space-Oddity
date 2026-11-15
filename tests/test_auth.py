"""Tests for services/auth.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.generacion.perfiles import DEMO_PASSWORD, DOCTOR
from services.auth import MAX_FAILED_ATTEMPTS, authenticate, hash_password, verify_password
from services.database import sesion

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def db_path(mission_copy) -> Path:
    return mission_copy(16, 5)


def test_hash_is_salted_and_verifiable() -> None:
    first, second = hash_password("secreto", iterations=1000), hash_password("secreto", iterations=1000)
    assert first != second                                   # Different salt each time
    assert verify_password("secreto", first) and not verify_password("otro", first)
    assert not verify_password("secreto", None) and not verify_password("secreto", "basura")


def test_login_ok_returns_user_without_hash(db_path: Path) -> None:
    result = authenticate(DOCTOR[1].upper(), DEMO_PASSWORD, db_path=db_path, now=NOW)
    assert result.ok and result.user["role"] == 1
    assert "password_hash" not in result.user


def test_wrong_password_and_unknown_email_give_same_message(db_path: Path) -> None:
    wrong = authenticate(DOCTOR[1], "mala", db_path=db_path, now=NOW)
    unknown = authenticate("nadie@x.com", "mala", db_path=db_path, now=NOW)
    assert not wrong.ok and wrong.error == unknown.error


def test_lockout_after_repeated_failures(db_path: Path) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        authenticate(DOCTOR[1], "mala", db_path=db_path, now=NOW)
    locked = authenticate(DOCTOR[1], DEMO_PASSWORD, db_path=db_path, now=NOW + timedelta(minutes=1))
    assert not locked.ok and "locked" in locked.error
    assert authenticate(DOCTOR[1], DEMO_PASSWORD, db_path=db_path, now=NOW + timedelta(minutes=16)).ok
    with sesion(db_path) as db:
        actions = [a["action"] for a in db.auditoria.listar()]
    assert actions.count("login_failed") == MAX_FAILED_ATTEMPTS and actions[-1] == "login_ok"
