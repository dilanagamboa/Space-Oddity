"""Daily reminder service using Strategy + Registry patterns.

Reads ``mission_logs.active_monitoring_mode`` and schedules daily reminders for
medications, meals, the doctor's notices and clinical sampling (glucose, saliva,
bioimpedance chair).

Patterns:
    * **Strategy + Registry**: ``UserItemStrategy`` handles items the doctor assigns
      (pills, meals, notices) on the chosen weekdays and ``SamplingStrategy``
      handles sampling schedules driven by the monitoring mode. Every strategy is
      registered in ``STRATEGIES``. ``task`` reminders are never created (``id_task`` stays NULL).
    * **Idempotent**: running ``generate_daily_reminders`` twice on the same day
      never duplicates rows.

The standard-mode schedule comes from ``core.protocolo``, the same plan the data
generator follows. Reminder types, modes and statuses come from ``core.enums``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, ClassVar

from core.enums import EstadoRecordatorio, ModoMonitoreo, TipoRecordatorio
from core.protocolo import (
    CHAIR_AT,
    CHAIR_WEEKDAY,
    GLUCOSE_FASTING_AT,
    GLUCOSE_POST_MEAL_AT,
    GLUCOSE_WEEKDAYS,
    SALIVA_AT,
    SALIVA_WEEKDAY,
    HourMinute,
    mission_date,
    mission_moment,
    runs_on,
    weekday,
)
from services.database import sesion

Row = dict[str, Any]
SOURCE_FIELDS = ("id_pill", "id_meal", "id_notice", "id_task")
Slots = tuple[tuple[HourMinute, str], ...]


# =====================================================================
# 1. PLANNING CONTEXT
# =====================================================================

@dataclass(frozen=True)
class PlanningContext:
    """Mission day context passed to every reminder strategy."""

    id_mission_log: int
    mission_day: int
    start_date: str
    mode: ModoMonitoreo
    pills: Sequence[Mapping[str, Any]]
    meals: Sequence[Mapping[str, Any]]
    notices: Sequence[Mapping[str, Any]]

    def row(self, r_type: TipoRecordatorio, at: HourMinute, title: str, instructions: str,
            **source: int) -> Row:
        """Builds one reminder row; ``source`` is the item's FK (``id_pill``, ``id_meal`` or ``id_notice``).
        ``id_task`` is always NULL in the current scope."""
        return {
            "id_mission_log": self.id_mission_log,
            "reminder_type": r_type,
            **dict.fromkeys(SOURCE_FIELDS),
            **source,
            "title": title,
            "instructions": instructions,
            "monitoring_mode": self.mode,
            "mission_day": self.mission_day,
            "scheduled_at": mission_moment(self.start_date, self.mission_day, at),
            "status": EstadoRecordatorio.PENDIENTE,
            "is_simulated": 0,
        }


def _hm(value: Any) -> HourMinute:
    """(hour, minute) from a ``time`` object or an 'HH:MM' string."""
    if isinstance(value, time):
        return value.hour, value.minute
    hour, minute, *_ = str(value).strip().split(":")
    return int(hour), int(minute)


# =====================================================================
# 2. STRATEGIES & REGISTRY
# =====================================================================

class ReminderStrategy(ABC):
    """Base strategy registered in ``STRATEGIES``."""

    reminder_type: ClassVar[TipoRecordatorio]

    @abstractmethod
    def build(self, ctx: PlanningContext) -> list[Row]:
        """Candidate reminder rows for ``ctx.mission_day``."""


class UserItemStrategy(ReminderStrategy):
    """Shared template for active user items (pills, meals, notices) on their weekdays."""

    source_attr: ClassVar[str]
    fk_field: ClassVar[str]

    def build(self, ctx: PlanningContext) -> list[Row]:
        items: Sequence[Mapping[str, Any]] = getattr(ctx, self.source_attr)
        return [
            ctx.row(self.reminder_type, _hm(item["scheduled_time"]), *self._format(item),
                    **{self.fk_field: int(item["id"])})
            for item in items
            if runs_on(int(item["weekdays"]), mission_date(ctx.start_date, ctx.mission_day))
        ]

    @abstractmethod
    def _format(self, item: Mapping[str, Any]) -> tuple[str, str]:
        """(title, instructions) in English for the item."""


class PillStrategy(UserItemStrategy):
    reminder_type = TipoRecordatorio.PASTILLA
    source_attr = "pills"
    fk_field = "id_pill"

    def _format(self, item: Mapping[str, Any]) -> tuple[str, str]:
        dose = f" ({item['dose']})" if item.get("dose") else ""
        return f"Medication: {item['name']}{dose}", str(item.get("description") or "Take with water.")


class MealStrategy(UserItemStrategy):
    reminder_type = TipoRecordatorio.COMIDA
    source_attr = "meals"
    fk_field = "id_meal"

    def _format(self, item: Mapping[str, Any]) -> tuple[str, str]:
        kcal, protein = item.get("target_calories_kcal"), item.get("target_protein_g")
        target = f" ({kcal:.0f} kcal, {protein:.0f} g protein)" if kcal and protein else ""
        return f"Meal: {item['name']}", f"Log caloric intake{target}."


class NoticeStrategy(UserItemStrategy):
    reminder_type = TipoRecordatorio.AVISO
    source_attr = "notices"
    fk_field = "id_notice"

    def _format(self, item: Mapping[str, Any]) -> tuple[str, str]:
        return str(item["name"]), str(item.get("message") or "")


class SamplingStrategy(ReminderStrategy):
    """Data-driven template: one schedule per monitoring mode, ``standard`` as fallback."""

    title: ClassVar[str]
    schedules: ClassVar[dict[ModoMonitoreo, Slots]]

    def is_active_day(self, ctx: PlanningContext) -> bool:
        return True

    def build(self, ctx: PlanningContext) -> list[Row]:
        if not self.is_active_day(ctx):
            return []
        slots = self.schedules.get(ctx.mode, self.schedules[ModoMonitoreo.ESTANDAR])
        return [ctx.row(self.reminder_type, at, self.title, text) for at, text in slots]


class GlucoseStrategy(SamplingStrategy):
    reminder_type = TipoRecordatorio.MUESTRA_GLUCOSA
    title = "Capillary blood glucose sample"
    schedules = {
        ModoMonitoreo.ALERTA_INFECCION: (                   # 6/day, intensive sepsis monitoring
            (GLUCOSE_FASTING_AT, "Fasting blood glucose (intensive infection protocol)."),
            ((9, 30), "Blood glucose 2 h post-breakfast."),
            ((12, 0), "Pre-lunch blood glucose."),
            ((14, 30), "Blood glucose 2 h post-lunch."),
            ((18, 30), "Pre-dinner blood glucose."),
            ((21, 30), "Nighttime blood glucose."),
        ),
        ModoMonitoreo.ALERTA_ATROFIA: (                     # 2/day, metabolic check
            (GLUCOSE_FASTING_AT, "Fasting blood glucose (atrophy metabolic check)."),
            ((14, 30), "Blood glucose 2 h post-lunch."),
        ),
        ModoMonitoreo.ALERTA_CIRCADIANA: (                  # 2/day, circadian check
            (GLUCOSE_FASTING_AT, "Fasting blood glucose (circadian check)."),
            ((20, 30), "Blood glucose 2 h post-dinner."),
        ),
        ModoMonitoreo.ESTANDAR: (                           # Team sampling plan (core.protocolo)
            (GLUCOSE_FASTING_AT, "Fasting capillary blood glucose."),
            *zip(GLUCOSE_POST_MEAL_AT, ("Blood glucose 2 h post-breakfast.", "Blood glucose 2 h post-lunch.",
                                        "Blood glucose 2 h post-dinner."), strict=True),
        ),
    }

    def is_active_day(self, ctx: PlanningContext) -> bool:
        return ctx.mode != ModoMonitoreo.ESTANDAR or weekday(ctx.mission_day) in GLUCOSE_WEEKDAYS


class SalivaStrategy(SamplingStrategy):
    reminder_type = TipoRecordatorio.MUESTRA_SALIVA
    title = "Saliva biomarker sample"
    _FOUR_POINT: ClassVar[Slots] = tuple(zip(SALIVA_AT.values(), (
        "Saliva upon awakening (0 min).",
        "Saliva +30 min post-awakening (CAR curve).",
        "Afternoon saliva (cortisol and sAA).",
        "Bedtime saliva (Cortisol/DHEA ratio).",
    ), strict=True))
    schedules = {
        ModoMonitoreo.ALERTA_CIRCADIANA: _FOUR_POINT,
        ModoMonitoreo.ALERTA_INFECCION: (
            (SALIVA_AT["wake_0m"], "Morning saliva (immunology/sAA)."),
            ((15, 0), "Afternoon saliva."),
            (SALIVA_AT["bedtime"], "Nighttime saliva."),
        ),
        ModoMonitoreo.ALERTA_ATROFIA: (
            (SALIVA_AT["wake_0m"], "Morning saliva (C/DHEA balance)."),
            (SALIVA_AT["bedtime"], "Nighttime saliva."),
        ),
        ModoMonitoreo.ESTANDAR: _FOUR_POINT,
    }

    def is_active_day(self, ctx: PlanningContext) -> bool:
        if ctx.mode == ModoMonitoreo.ESTANDAR:
            return weekday(ctx.mission_day) == SALIVA_WEEKDAY
        if ctx.mode == ModoMonitoreo.ALERTA_ATROFIA:
            return (ctx.mission_day - 1) % 3 == 0          # Every 3 days in atrophy mode
        return True                                       # Daily in infection and circadian modes


class BiaChairStrategy(SamplingStrategy):
    reminder_type = TipoRecordatorio.PESAJE_BIA
    title = "Bioimpedance chair (BIA) measurement"
    schedules = {
        ModoMonitoreo.ESTANDAR: (
            (CHAIR_AT, "Measure fasting body composition (mass, % muscle and visceral fat)."),
        ),
    }

    def is_active_day(self, ctx: PlanningContext) -> bool:
        return ctx.mode == ModoMonitoreo.ALERTA_ATROFIA or weekday(ctx.mission_day) == CHAIR_WEEKDAY


STRATEGIES: dict[TipoRecordatorio, ReminderStrategy] = {
    s.reminder_type: s
    for s in (PillStrategy(), MealStrategy(), NoticeStrategy(), GlucoseStrategy(), SalivaStrategy(),
              BiaChairStrategy())
}


# =====================================================================
# 3. PUBLIC SERVICE ENTRY POINTS
# =====================================================================

def _key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Natural deduplication key (idempotency across re-runs)."""
    return (str(row["reminder_type"]), str(row["scheduled_at"])[:19].replace(" ", "T"),
            *(row.get(field) for field in SOURCE_FIELDS))


def generate_daily_reminders(
    id_mission_log: int,
    mission_day: int,
    *,
    db_path: Path | str | None = None,
) -> list[Row]:
    """Creates the day's reminders according to ``active_monitoring_mode`` (idempotent)."""
    with sesion(db_path) as db:
        log = db.mission_logs.obtener(id_mission_log)
        if log is None:
            raise ValueError(f"mission_log {id_mission_log} does not exist.")
        ctx = PlanningContext(
            id_mission_log=id_mission_log,
            mission_day=mission_day,
            start_date=log["start_date"],
            mode=ModoMonitoreo(log["active_monitoring_mode"] or ModoMonitoreo.ESTANDAR),
            pills=db.pastillas.activas_de(log["id_user"]),
            meals=db.comidas.activas_de(log["id_user"]),
            notices=db.avisos.activas_de(log["id_user"]),
        )
        existing = db.recordatorios.listar(id_mission_log=id_mission_log, mission_day=mission_day)
        seen = {_key(r) for r in existing}
        created: list[Row] = []
        for strategy in STRATEGIES.values():
            for row in strategy.build(ctx):
                if (key := _key(row)) not in seen:
                    seen.add(key)
                    created.append({"id": db.recordatorios.insertar(**row), **row})
    return created


def complete_reminder(
    reminder_id: int,
    *,
    status: EstadoRecordatorio | str = EstadoRecordatorio.COMPLETADO,
    intake_pct: float | None = None,
    db_path: Path | str | None = None,
    now: datetime | None = None,
) -> Row:
    """Sets a reminder's status ('completed' | 'skipped' | 'pending')."""
    status = EstadoRecordatorio(status)                      # Raises ValueError if invalid
    completed_at = (now or datetime.now(UTC)) if status == EstadoRecordatorio.COMPLETADO else None
    with sesion(db_path) as db:
        db.recordatorios.actualizar(
            reminder_id, status=status, completed_at=completed_at, intake_pct=intake_pct
        )
        return db.recordatorios.obtener(reminder_id)


def overdue_pill_reminders(
    id_mission_log: int,
    *,
    overdue_minutes: int = 60,
    db_path: Path | str | None = None,
    now: datetime | None = None,
) -> list[Row]:
    """Pending pill reminders at least ``overdue_minutes`` late (notification R1-R5)."""
    limit = (now or datetime.now(UTC)) - timedelta(minutes=overdue_minutes)
    with sesion(db_path) as db:
        pending = db.recordatorios.pendientes_hasta(id_mission_log, limit)
    return [r for r in pending if r["reminder_type"] == TipoRecordatorio.PASTILLA]
