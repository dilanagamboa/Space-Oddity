"""Flight surgeon actions: prescribe pills, plan meals and send notices.

Every action is validated, stored in one transaction and written to ``audit_log``.
Pills, meals and notices share the same commands (``ItemKind``): they differ only
in their table and fields. All of them repeat on the chosen weekdays (bit mask).

Current scope: one flight surgeon for the whole crew.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import date, time
from pathlib import Path
from typing import Any

from services.database import sesion
from services.vistas import ITEM_REPOS, ItemKind

# Fields each kind accepts from the forms (anything else is ignored)
ITEM_FIELDS: dict[ItemKind, tuple[str, ...]] = {
    "pill": ("name", "dose", "description", "scheduled_time", "weekdays", "start_date", "end_date"),
    "meal": ("name", "meal_type", "target_calories_kcal", "target_protein_g", "scheduled_time", "weekdays"),
    "notice": ("name", "message", "scheduled_time", "weekdays"),
}
SIGNED_KINDS: frozenset[ItemKind] = frozenset({"pill", "notice"})   # Tables with ``assigned_by``


def _validate(kind: ItemKind, values: Mapping[str, Any]) -> dict[str, Any]:
    data = {k: values.get(k) for k in ITEM_FIELDS[kind]}
    if not str(data["name"] or "").strip():
        raise ValueError("The name is required.")
    if not isinstance(data["scheduled_time"], time):
        raise ValueError("Set the time.")
    if not data["weekdays"]:
        raise ValueError("Select at least one weekday.")
    start, end = data.get("start_date"), data.get("end_date")
    if isinstance(start, date) and isinstance(end, date) and end < start:
        raise ValueError("The end date cannot be before the start date.")
    data["name"] = str(data["name"]).strip()
    return data


def save_item(
    kind: ItemKind,
    values: Mapping[str, Any],
    *,
    id_user: int,
    doctor_id: int,
    item_id: int | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Creates (``item_id=None``) or updates a pill, meal or notice. Returns its id.

    Raises:
        ValueError: Invalid form data, with a message ready for the UI.
    """
    data = _validate(kind, values)
    if kind in SIGNED_KINDS:
        data["assigned_by"] = doctor_id
    try:
        with sesion(db_path) as db:
            repo = getattr(db, ITEM_REPOS[kind])
            if item_id is None:
                item_id = repo.insertar(id_user=id_user, **data)
                action = f"{kind}_assigned"
            else:
                repo.actualizar(item_id, **data)
                action = f"{kind}_updated"
            db.auditoria.registrar(doctor_id, action, ITEM_REPOS[kind], item_id, {"name": data["name"]})
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Invalid data: {exc}") from exc
    return item_id


def retire_item(kind: ItemKind, item_id: int, *, doctor_id: int, db_path: Path | str | None = None) -> None:
    """Deactivates a pill, meal or notice (kept for history, no new reminders)."""
    with sesion(db_path) as db:
        getattr(db, ITEM_REPOS[kind]).actualizar(item_id, active=0)
        db.auditoria.registrar(doctor_id, f"{kind}_retired", ITEM_REPOS[kind], item_id)

