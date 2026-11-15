"""Daily reminder service using Strategy + Registry patterns."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, ClassVar

from config import DB_PATH
from services.database import sesion

Row = dict[str, Any]


@dataclass(frozen=True)
class PlanningContext:
    """Mission day context passed to every reminder strategy."""

    id_mission_log: int
    mission_day: int
    day_date: date
    mode: str
    pills: Sequence[Mapping[str, Any]]
    meals: Sequence[Mapping[str, Any]]

    def row(self, r_type: str, hm: tuple[int, int], title: str, instructions: str,
            *, id_pill: int | None = None, id_meal: int | None = None) -> Row:
        """Builds a single reminder dictionary with `id_task = None`."""
        return {
            "id_mission_log": self.id_mission_log,
            "reminder_type": r_type,
            "id_pill": id_pill,
            "id_meal": id_meal,
            "id_task": None,
            "title": title,
            "instructions": instructions,
            "monitoring_mode": self.mode,
            "mission_day": self.mission_day,
            "scheduled_at": datetime.combine(self.day_date, time(*hm), tzinfo=UTC),
            "status": "pending",
            "is_simulated": 0,
        }


def _hm(val: Any) -> tuple[int, int]:
    """Extracts (hour, minute) from a `time` object or 'HH:MM' string."""
    if isinstance(val, time):
        return val.hour, val.minute
    h, m, *_ = str(val).strip().split(":")
    return int(h), int(m)


# =====================================================================
# 1. STRATEGIES & REGISTRY (No duplicate loops or dead branches)
# =====================================================================

class ReminderStrategy(ABC):
    """Base strategy registered automatically in `STRATEGIES`."""

    reminder_type: ClassVar[str]

    @abstractmethod
    def build(self, ctx: PlanningContext) -> list[Row]:
        """Returns the reminders for `ctx.mission_day`."""


class UserItemStrategy(ReminderStrategy):
    """Shared template for user-scheduled DB items (`pills` and `meals`)."""

    source_attr: ClassVar[str]
    fk_field: ClassVar[str]

    def build(self, ctx: PlanningContext) -> list[Row]:
        items: Sequence[Mapping[str, Any]] = getattr(ctx, self.source_attr)
        return [
            ctx.row(self.reminder_type, _hm(item["scheduled_time"]), *self._format(item),
                    **{self.fk_field: int(item["id"])})
            for item in items
            if item.get("active", 1) and (ctx.mission_day - 1) % max(1, int(item.get("frequency_days", 1))) == 0
        ]

    @abstractmethod
    def _format(self, item: Mapping[str, Any]) -> tuple[str, str]:
        """Returns (Spanish title, Spanish instructions) for the item."""


class PillStrategy(UserItemStrategy):
    reminder_type = "pill"
    source_attr = "pills"
    fk_field = "id_pill"

    def _format(self, item: Mapping[str, Any]) -> tuple[str, str]:
        dose = f" ({item['dose']})" if item.get("dose") else ""
        return f"Medicamento: {item['name']}{dose}", str(item.get("description") or "Tomar con agua.")


class MealStrategy(UserItemStrategy):
    reminder_type = "meal"
    source_attr = "meals"
    fk_field = "id_meal"

    def _format(self, item: Mapping[str, Any]) -> tuple[str, str]:
        kcal, prot = item.get("target_calories_kcal"), item.get("target_protein_g")
        meta = f" ({kcal:.0f} kcal, {prot:.0f} g proteína)" if kcal and prot else ""
        return f"Comida: {item['name']}", f"Registrar ingesta calórica{meta}."


class SamplingStrategy(ReminderStrategy):
    """Shared data-driven template for clinical sampling (`glucose`, `saliva`, `bia_chair`)."""

    title: ClassVar[str]
    schedules: ClassVar[dict[str, tuple[tuple[tuple[int, int], str], ...]]]

    def is_active_day(self, ctx: PlanningContext) -> bool:
        return True

    def build(self, ctx: PlanningContext) -> list[Row]:
        if not self.is_active_day(ctx):
            return []
        slots = self.schedules.get(ctx.mode, self.schedules["standard"])
        return [ctx.row(self.reminder_type, hm, self.title, txt) for hm, txt in slots]


class GlucoseStrategy(SamplingStrategy):
    reminder_type = "glucose"
    title = "Muestra de glucosa capilar"
    schedules = {
        "alert_infection": (
            ((6, 30), "Glucosa en ayunas (protocolo intensivo de infección)."),
            ((9, 30), "Glucosa 2 h post-desayuno."),
            ((12, 0), "Glucosa pre-almuerzo."),
            ((14, 30), "Glucosa 2 h post-almuerzo."),
            ((18, 30), "Glucosa pre-cena."),
            ((21, 30), "Glucosa nocturna."),
        ),
        "alert_atrophy": (
            ((6, 30), "Glucosa en ayunas (control metabólico por atrofia)."),
            ((14, 30), "Glucosa 2 h post-almuerzo."),
        ),
        "alert_circadian": (
            ((6, 30), "Glucosa en ayunas (control circadiano)."),
            ((20, 30), "Glucosa 2 h post-cena."),
        ),
        "standard": (
            ((6, 30), "Glucosa capilar en ayunas."),
            ((9, 30), "Glucosa 2 h post-desayuno."),
            ((14, 30), "Glucosa 2 h post-almuerzo."),
            ((20, 30), "Glucosa 2 h post-cena."),
        ),
    }

    def is_active_day(self, ctx: PlanningContext) -> bool:
        return ctx.mode != "standard" or (ctx.mission_day % 7 in (1, 4))


class SalivaStrategy(SamplingStrategy):
    reminder_type = "saliva"
    title = "Muestra de saliva"
    _FOUR_POINT = (
        ((6, 0), "Saliva al despertar (0 min)."),
        ((6, 30), "Saliva +30 min post-despertar (curva CAR)."),
        ((16, 0), "Saliva de tarde (cortisol y sAA)."),
        ((21, 45), "Saliva antes de dormir (ratio Cortisol/DHEA)."),
    )
    schedules = {
        "alert_circadian": _FOUR_POINT,
        "alert_infection": (
            ((6, 0), "Saliva matutina (inmunología/sAA)."),
            ((15, 0), "Saliva vespertina."),
            ((21, 45), "Saliva nocturna."),
        ),
        "alert_atrophy": (
            ((6, 0), "Saliva matutina (balance C/DHEA)."),
            ((21, 45), "Saliva nocturna."),
        ),
        "standard": _FOUR_POINT,
    }

    def is_active_day(self, ctx: PlanningContext) -> bool:
        if ctx.mode == "standard":
            return ctx.mission_day % 7 == 3
        if ctx.mode == "alert_atrophy":
            return (ctx.mission_day - 1) % 3 == 0
        return True


class BiaChairStrategy(SamplingStrategy):
    reminder_type = "bia_chair"
    title = "Medición en silla de bioimpedancia (BIA)"
    schedules = {
        "standard": (
            ((6, 15), "Medir composición corporal en ayunas (masa, % músculo y grasa visceral)."),
        ),
    }

    def is_active_day(self, ctx: PlanningContext) -> bool:
        return ctx.mode == "alert_atrophy" or (ctx.mission_day % 7 == 1)


STRATEGIES: dict[str, ReminderStrategy] = {
    s.reminder_type: s
    for s in (PillStrategy(), MealStrategy(), GlucoseStrategy(), SalivaStrategy(), BiaChairStrategy())
}


# =====================================================================
# 2. PUBLIC FUNCTIONS
# =====================================================================

def _key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Natural deduplication key for idempotency."""
    return (row["reminder_type"], str(row["scheduled_at"])[:19], row.get("id_pill"), row.get("id_meal"))


def generate_daily_reminders(
    id_mission_log: int,
    mission_day: int,
    *,
    db_path: Path | str = DB_PATH,
) -> list[Row]:
    """Generates and stores daily reminders idempotently based on `active_monitoring_mode`."""
    with sesion(db_path) as db:
        log = db.mission_logs.obtener(id_mission_log)
        if log is None:
            raise ValueError(f"mission_log {id_mission_log} does not exist.")
        id_user = int(log["id_user"])
        start = date.fromisoformat(str(log["start_date"])[:10])
        ctx = PlanningContext(
            id_mission_log=id_mission_log,
            mission_day=mission_day,
            day_date=start + timedelta(days=mission_day - 1),
            mode=str(log.get("active_monitoring_mode") or "standard"),
            pills=db.pastillas.listar(id_user=id_user),
            meals=db.comidas.listar(id_user=id_user),
        )
        seen = {_key(r) for r in db.recordatorios.listar(id_mission_log=id_mission_log, mission_day=mission_day)}
        created: list[Row] = []
        for strategy in STRATEGIES.values():
            for row in strategy.build(ctx):
                if (k := _key(row)) not in seen:
                    seen.add(k)
                    created.append({"id": db.recordatorios.insertar(**row), **row})
    return created


def complete_reminder(
    reminder_id: int,
    *,
    status: str = "completed",
    intake_pct: float | None = None,
    db_path: Path | str = DB_PATH,
    now: datetime | None = None,
) -> Row:
    """Updates reminder status ('completed' | 'skipped' | 'pending') idempotently."""
    if status not in {"pending", "completed", "skipped"}:
        raise ValueError(f"Invalid status: {status}")
    completed_at = (now or datetime.now(UTC)) if status == "completed" else None
    with sesion(db_path) as db:
        db.recordatorios.actualizar(reminder_id, status=status, completed_at=completed_at, intake_pct=intake_pct)
        return db.recordatorios.obtener(reminder_id)


def overdue_pill_reminders(
    id_mission_log: int,
    mission_day: int,
    *,
    overdue_minutes: int = 60,
    db_path: Path | str = DB_PATH,
    now: datetime | None = None,
) -> list[Row]:
    """Returns pending pill reminders that are >= `overdue_minutes` past `scheduled_at`."""
    current = (now or datetime.now(UTC)).replace(tzinfo=None)
    limit = timedelta(minutes=overdue_minutes)
    with sesion(db_path) as db:
        rows = db.recordatorios.listar(id_mission_log=id_mission_log, mission_day=mission_day)
    return [
        r for r in rows
        if r["reminder_type"] == "pill"
        and r["status"] == "pending"
        and (current - datetime.fromisoformat(str(r["scheduled_at"]).replace("Z", "+00:00")).replace(tzinfo=None)) >= limit
    ]
