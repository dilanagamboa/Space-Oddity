"""Builds clinical alerts from the evaluation produced by ``core/rangos.py``.

Pipeline::

    rangos.evaluate_*()  ->  rangos.evaluate_overall_health()  ->  EvaluationResult
          |
          v
    build_alerts()       ->  list[AlertDraft]   (one per notification ID: C1, F1, G1, S4...)
          |
          v
    filter_repeated()    ->  drops alerts already sent in the last 24 h (unless escalated)
          |
          v
    AlertDraft.to_db_row()  ->  dict ready for ``AlertaRepo.insertar(**row)``

Notification rules (team document, "Notificaciones"):
    * Red alerts are sent immediately. Yellow alerts go in the daily report.
    * The same alert is not repeated within 24 h unless it escalates from yellow to red.

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core.planes import PlanSeverity, Recipient, get_action_plan
from core.rangos import EvaluationResult, SamplingMode, TriggeredRule

REPEAT_WINDOW_HOURS: float = 24.0

# Notification ID -> value of ``alerts.alert_category`` in the database.
ALERT_CATEGORY_BY_NOTIFICATION: dict[str, str] = {
    "C1": "atrophy_glim",
    "C2": "atrophy_glim",
    "C3": "atrophy_glim",
    "F1": "hpa_circadian",
    "G1": "sepsis_infection",
    "S1": "glycemic",
    "S2": "cardiovascular",
    "S3": "anemia_oxygen",
    "S4": "radiation",
}

# Rule category (rangos) -> sampling mode the alert triggers.
SAMPLING_MODE_BY_CATEGORY: dict[str, SamplingMode | None] = {
    "C": "alert_atrophy",
    "F": "alert_circadian",
    "G": "alert_infection",
    "S": None,
}

_SEVERITY_RANK: dict[str, int] = {"normal": 0, "yellow": 1, "orange": 2, "red": 3}


@dataclass(frozen=True)
class AlertDraft:
    """Alert ready to be stored and notified.

    Attributes:
        mission_day: Mission day the alert belongs to.
        notification_id: ID from the notifications table (e.g. "G1").
        alert_category: Database category (e.g. "sepsis_infection").
        severity: "yellow" or "red".
        title: Short title from the action plan.
        triggered_rules: Rules that caused the alert.
        action_plan_text: Numbered plan for this severity.
        recipients: Who must be notified.
        sampling_mode_triggered: Sampling mode the alert activates, if any.
        infographic_asset: Image of the action plan, if any.
    """

    mission_day: int
    notification_id: str
    alert_category: str
    severity: PlanSeverity
    title: str
    triggered_rules: tuple[TriggeredRule, ...]
    action_plan_text: str
    recipients: tuple[Recipient, ...]
    sampling_mode_triggered: SamplingMode | None
    infographic_asset: str | None

    @property
    def is_immediate(self) -> bool:
        """Red alerts are sent right away. Yellow ones wait for the daily report."""
        return self.severity == "red"

    def to_db_row(self, id_mission_log: int, *, is_simulated: bool = False) -> dict[str, Any]:
        """Builds the columns for ``AlertaRepo.insertar(**row)``.

        ``triggered_rules_json`` stores the notification ID so ``filter_repeated``
        can recognize the same alert later.
        """
        return {
            "id_mission_log": id_mission_log,
            "mission_day": self.mission_day,
            "alert_category": self.alert_category,
            "severity": self.severity,
            "title": self.title,
            "triggered_rules_json": {
                "notification_id": self.notification_id,
                "recipients": list(self.recipients),
                "rules": [asdict(rule) for rule in self.triggered_rules],
            },
            "action_plan_text": self.action_plan_text,
            "infographic_asset": self.infographic_asset,
            "sampling_mode_triggered": self.sampling_mode_triggered,
            "is_simulated": int(is_simulated),
        }


# =====================================================================
# 1. BUILD
# =====================================================================

def _max_severity(rules: Sequence[TriggeredRule]) -> PlanSeverity:
    return "red" if any(rule.severity == "red" for rule in rules) else "yellow"


def build_alerts(evaluation: EvaluationResult, mission_day: int) -> list[AlertDraft]:
    """Groups the triggered rules by notification ID and attaches the action plan.

    Several rules with the same ID (for example BMI and mass loss, both C1) become
    a single alert whose severity is the highest among them.

    Args:
        evaluation: Result of ``rangos.evaluate_overall_health``.
        mission_day: Mission day being evaluated.

    Returns:
        One ``AlertDraft`` per notification ID, red alerts first.
    """
    grouped: dict[str, list[TriggeredRule]] = {}
    for rule in evaluation.triggered_rules:
        if rule.severity in ("yellow", "red"):
            grouped.setdefault(rule.notification_id, []).append(rule)

    drafts: list[AlertDraft] = []
    for notification_id, rules in grouped.items():
        plan = get_action_plan(notification_id)
        severity = _max_severity(rules)
        drafts.append(
            AlertDraft(
                mission_day=mission_day,
                notification_id=notification_id,
                alert_category=ALERT_CATEGORY_BY_NOTIFICATION[notification_id],
                severity=severity,
                title=plan.title,
                triggered_rules=tuple(rules),
                action_plan_text=plan.render(severity),
                recipients=plan.recipients(severity),
                sampling_mode_triggered=SAMPLING_MODE_BY_CATEGORY[rules[0].category],
                infographic_asset=plan.infographic_asset,
            )
        )

    drafts.sort(key=lambda d: (-_SEVERITY_RANK[d.severity], d.notification_id))
    return drafts


# =====================================================================
# 2. ANTI-REPETITION FILTER (24 h)
# =====================================================================

def _to_utc_naive(moment: datetime | str) -> datetime:
    """Normalizes a datetime or ISO string to naive UTC (how the database stores it)."""
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _notification_id_of(row: Mapping[str, Any]) -> str | None:
    rules = row.get("triggered_rules_json")
    if isinstance(rules, Mapping):
        return rules.get("notification_id")
    return None


def filter_repeated(
    drafts: Sequence[AlertDraft],
    recent_alerts: Sequence[Mapping[str, Any]],
    now: datetime,
    window_hours: float = REPEAT_WINDOW_HOURS,
) -> list[AlertDraft]:
    """Drops alerts already issued inside the window, unless they escalated.

    Args:
        drafts: Alerts built for today.
        recent_alerts: Rows from the ``alerts`` table for the same mission log
            (as returned by ``AlertaRepo``: dicts with ``created_at``, ``severity``
            and ``triggered_rules_json``).
        now: Current moment.
        window_hours: Repetition window. 24 h according to the team document.

    Returns:
        Alerts that must be stored and notified.
    """
    limit = _to_utc_naive(now) - timedelta(hours=window_hours)

    highest_recent: dict[str, int] = {}
    for row in recent_alerts:
        notification_id = _notification_id_of(row)
        if notification_id is None or _to_utc_naive(row["created_at"]) < limit:
            continue
        rank = _SEVERITY_RANK.get(str(row["severity"]), 0)
        highest_recent[notification_id] = max(rank, highest_recent.get(notification_id, 0))

    return [
        draft
        for draft in drafts
        if _SEVERITY_RANK[draft.severity] > highest_recent.get(draft.notification_id, 0)
    ]
