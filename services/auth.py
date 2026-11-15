"""Authentication: password hashing, login and account lockout.

Passwords are stored as PBKDF2-HMAC-SHA512 hashes with a random salt per user
(standard library, no extra dependency). After ``MAX_FAILED_ATTEMPTS`` wrong
passwords the account is locked for ``LOCK_MINUTES``. Every attempt is written to
``audit_log``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.database import sesion

ALGORITHM = "pbkdf2_sha512"
ITERATIONS = 210_000                 # OWASP 2023 recommendation for PBKDF2-HMAC-SHA512
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    """Returns ``algorithm$iterations$salt$hash`` for storing in ``users.password_hash``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of a password against a stored hash."""
    try:
        algorithm, iterations, salt, expected = (stored or "").split("$")
    except ValueError:
        return False
    if algorithm != ALGORITHM:
        return False
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


@dataclass(frozen=True)
class LoginResult:
    """Outcome of a login attempt. ``user`` is set only on success."""

    user: dict[str, Any] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.user is not None


def _as_utc(moment: str | None) -> datetime | None:
    return datetime.fromisoformat(moment).replace(tzinfo=UTC) if moment else None


def authenticate(
    email: str,
    password: str,
    *,
    db_path: Path | str | None = None,
    now: datetime | None = None,
) -> LoginResult:
    """Validates the credentials and updates the lockout counters.

    The error message never reveals whether the email exists.
    """
    now = now or datetime.now(UTC)
    invalid = LoginResult(error="Wrong email or password.")
    with sesion(db_path) as db:
        user = db.usuarios.por_email(email)
        if user is None or not user["active"]:
            return invalid
        locked_until = _as_utc(user["locked_until"])
        if locked_until and locked_until > now:
            return LoginResult(error="Account temporarily locked. Try again later.")

        if not verify_password(password, user["password_hash"]):
            attempts = user["failed_attempts"] + 1
            lock = now + timedelta(minutes=LOCK_MINUTES) if attempts >= MAX_FAILED_ATTEMPTS else None
            db.usuarios.actualizar(user["id"], failed_attempts=0 if lock else attempts, locked_until=lock)
            db.auditoria.registrar(user["id"], "login_failed", "users", user["id"], {"locked": bool(lock)})
            return invalid

        db.usuarios.actualizar(user["id"], failed_attempts=0, locked_until=None)
        db.auditoria.registrar(user["id"], "login_ok", "users", user["id"])
    return LoginResult(user={k: user[k] for k in ("id", "full_name", "email", "role", "sex", "birth_date")})
