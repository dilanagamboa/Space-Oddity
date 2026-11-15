"""Daily reminder service using Strategy + Registry patterns."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, ClassVar

from config import DB_PATH
from services.database import Repositorios, sesion

Row = dict[str, Any]


@dataclass(frozen=True)
class PlanningContext:
    id_user: int
    id_mission_log: int
    mission_day: int
    day_date: date
    mode: str
    pills: Sequence[Mapping[str, Any]]
    meals: Sequence[Mapping[str, Any]]

    def at(self, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(self.day_date, time(hour, minute), tzinfo=UTC)


def _parse_hm(val: Any) -> tuple[int, int]:
    if isinstance(val, time):
        return val.hour, val.minute
    parts = str(val).strip().split(":")
    return int(parts[0]), int(parts[1])


class ReminderStrategy(ABC):
    """Base strategy for one reminder category (`task` is never registered)."""

    reminder_type: ClassVar[str]

    @abstractmethod
    def build(self, ctx: PlanningContext) -> list[Row]:
        raise NotImplementedError

    @staticmethod
    def _row(ctx: PlanningContext, r_type: str, hm: tuple[int, int], title: str,
             instructions: str, *, id_pill: int | None = None, id_meal: int | None = None) -> Row:
        return {
            "id_mission_log": ctx.id_mission_log,
            "reminder_type": r_type,
            "id_pill": id_pill,
            "id_meal": id_meal,
            "id_task": None,
            "title": title,
            "instructions": instructions,
            "monitoring_mode": ctx.mode,
            "mission_day": ctx.mission_day,
            "scheduled_at": ctx.at(*hm),
            "status": "pending",
            "is_simulated": 0,
        }


class PillStrategy(ReminderStrategy):
    reminder_type = "pill"

    def build(self, ctx: PlanningContext) -> list[Row]:
        rows: list[Row] = []
        for p in ctx.pills:
            if not p.get("active", 1) or (ctx.mission_day - 1) % max(1, int(p.get("frequency_days", 1))) != 0:
                continue
            dose = f" ({p['dose']})" if p.get("dose") else ""
            rows.append(self._row(
                ctx, self.reminder_type, _parse_hm(p.get("scheduled_time", "08:00")),
                f"Medicamento: {p['name']}{dose}",
                str(p.get("description") or "Tomar dosis programada con agua."),
                id_pill=int(p["id"]),
            ))
        return rows


class MealStrategy(ReminderStrategy):
    reminder_type = "meal"

    def build(self, ctx: PlanningContext) -> list[Row]:
        rows: list[Row] = []
        for m in ctx.meals:
            if not m.get("active", 1) or (ctx.mission_day - 1) % max(1, int(m.get("frequency_days", 1))) != 0:
                continue
            kcal, prot = m.get("target_calories_kcal"), m.get("target_protein_g")
            meta = f" ({kcal:.0f} kcal, {prot:.0f} g proteína)" if kcal and prot else ""
            rows.append(self._row(
                ctx, self.reminder_type, _parse_hm(m.get("scheduled_time", "12:30")),
                f"Comida: {m['name']}", f"Registrar ingesta calórica{meta}.", id_meal=int(m["id"]),
            ))
        return rows


class GlucoseStrategy(ReminderStrategy):
    reminder_type = "glucose"
    SLOTS: ClassVar[dict[str, tuple[tuple[tuple[int, int], str], ...]]] = {
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

    def build(self, ctx: PlanningContext) -> list[Row]:
        if ctx.mode == "standard" and ctx.mission_day % 7 not in (1, 4):
            return []
        slots = self.SLOTS.get(ctx.mode, self.SLOTS["standard"])
        return [self._row(ctx, self.reminder_type, hm, "Muestra de glucosa capilar", txt) for hm, txt in slots]


class SalivaStrategy(ReminderStrategy):
    reminder_type = "saliva"
    FOUR_POINT: ClassVar[tuple[tuple[tuple[int, int], str], ...]] = (
        ((6, 0), "Saliva al despertar (0 min)."),
        ((6, 30), "Saliva +30 min post-despertar (curva CAR)."),
        ((16, 0), "Saliva de tarde (cortisol y sAA)."),
        ((21, 45), "Saliva antes de dormir (ratio Cortisol/DHEA)."),
    )

    def build(self, ctx: PlanningContext) -> list[Row]:
        if ctx.mode == "alert_circadian":
            slots = self.FOUR_POINT
        elif ctx.mode == "alert_infection":
            slots = (((6, 0), "Saliva matutina (inmunología/sAA)."),
                     ((15, 0), "Saliva vespertina."), ((21, 45), "Saliva nocturna."))
        elif ctx.mode == "alert_atrophy":
            slots = (((6, 0), "Saliva matutina (balance C/DHEA)."),
                     ((21, 45), "Saliva nocturna.")) if (ctx.mission_day - 1) % 3 == 0 else ()
        else:
            slots = self.FOUR_POINT if ctx.mission_day % 7 == 3 else ()
        return [self._row(ctx, self.reminder_type, hm, "Muestra de saliva", txt) for hm, txt in slots]


class BiaChairStrategy(ReminderStrategy):
    reminder_type = "bia_chair"

    def build(self, ctx: PlanningContext) -> list[Row]:
        if ctx.mode != "alert_atrophy" and ctx.mission_day % 7 != 1:
            return []
        return [self._row(
            ctx, self.reminder_type, (6, 15),
            "Medición en silla de bioimpedancia (BIA)",
            "Medir composición corporal en ayunas (masa, % músculo y grasa visceral).",
        )]


STRATEGIES: dict[str, ReminderStrategy] = {
    s.reminder_type: s
    for s in (PillStrategy(), MealStrategy(), GlucoseStrategy(), SalivaStrategy(), BiaChairStrategy())
}


def _dedup_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    ts = str(row["scheduled_at"])[:19].replace("T", " ")
    return (row["reminder_type"], ts, row.get("id_pill"), row.get("id_meal"))


def _repo(db: Repositorios) -> Any:
    return getattr(db, "recordatorios", getattr(db, "reminders_log", None))


def generate_daily_reminders(
    id_mission_log: int,
    mission_day: int,
    *,
    db_path: Path | str = DB_PATH,
    now: datetime | None = None,
) -> list[Row]:
    """Creates daily reminders idempotently according to `mission_logs.active_monitoring_mode`."""
    with sesion(db_path) as db:
        log = db.mission_logs.obtener(id_mission_log)
        if log is None:
            raise ValueError(f"mission_log {id_mission_log} does not exist.")
        id_user = int(log["id_user"])
        start = date.fromisoformat(str(log["start_date"])[:10])
        ctx = PlanningContext(
            id_user=id_user,
            id_mission_log=id_mission_log,
            mission_day=mission_day,
            day_date=start + timedelta(days=mission_day - 1),
            mode=str(log.get("active_monitoring_mode") or "standard"),
            pills=db.pastillas.listar(id_user=id_user),
            meals=db.comidas.listar(id_user=id_user),
        )
        repo = _repo(db)
        existing = repo.listar(id_mission_log=id_mission_log, mission_day=mission_day)
        seen = {_dedup_key(r) for r in existing}

        created: list[Row] = []
        for strategy in STRATEGIES.values():
            for row in strategy.build(ctx):
                key = _dedup_key(row)
                if key in seen:
                    continue
                row_id = repo.insertar(**row)
                seen.add(key)
                created.append({"id": row_id, **row})
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
        repo = _repo(db)
        repo.actualizar(reminder_id, status=status, completed_at=completed_at, intake_pct=intake_pct)
        return repo.obtener(reminder_id)


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
    with sesion(db_path) as db:
        rows = _repo(db).listar(id_mission_log=id_mission_log, mission_day=mission_day)
    overdue: list[Row] = []
    for r in rows:
        if r["reminder_type"] != "pill" or r["status"] != "pending":
            continue
        sched = datetime.fromisoformat(str(r["scheduled_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
        if current - sched >= timedelta(minutes=overdue_minutes):
            overdue.append(r)
    return overdue
