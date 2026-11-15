"""Alert notification service using Strategy + Registry and Observer patterns.

Listens to `AlertEvent` published on `services.eventos.EventBus`, formats
clinical alert emails in English for the astronaut and flight surgeons (`Rol.DOCTOR`),
dispatches via the selected channel strategy, and marks `alerts.email_sent = 1`.

Patterns:
    * **Strategy + Registry**: each delivery channel is a `DeliveryChannel`
      subclass registered in `CHANNELS` (`email`, `log`).
    * **Observer**: `subscribe_notifications(bus, ...)` registers the handler.
    * **Idempotent**: checks `alerts.email_sent` before sending so an alert is
      never emailed twice.
"""

from __future__ import annotations

import smtplib
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import ClassVar

from core.enums import Rol
from services.database import sesion
from services.eventos import AlertEvent, EventBus, default_bus

# Type alias for injectable mail sender callable: (recipients, subject, body) -> bool
SenderFn = Callable[[list[str], str, str], bool]

# English labels for user-facing email notifications
SEVERITY_EN = {"red": "RED (Immediate Action)", "orange": "ORANGE", "yellow": "YELLOW"}
MODE_EN = {
    "standard": "Standard",
    "alert_infection": "Infection / Sepsis Alert",
    "alert_atrophy": "Atrophy / Malnutrition Alert",
    "alert_circadian": "Circadian / HPA Stress Alert",
}


# =====================================================================
# 1. DELIVERY CHANNELS (Strategy + Registry)
# =====================================================================

@dataclass(frozen=True)
class NotificationPayload:
    """Immutable value object holding the English notification ready for dispatch."""

    alert_id: int
    recipients: list[str]
    subject: str
    body: str


class DeliveryChannel(ABC):
    """Abstract base strategy for a notification delivery channel."""

    name: ClassVar[str]

    @abstractmethod
    def deliver(self, payload: NotificationPayload, sender: SenderFn | None = None) -> bool:
        """Delivers the payload and returns True on success."""


def smtp_send(
    recipients: list[str],
    subject: str,
    body: str,
    *,
    host: str = "smtp.gmail.com",
    port: int = 465,
    user: str | None = None,
    password: str | None = None,
) -> bool:
    """Default synchronous Gmail SMTP SSL sender (returns False if unconfigured)."""
    if not recipients or not user or not password:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    with smtplib.SMTP_SSL(host, port, timeout=10) as server:
        server.login(user, password)
        server.send_message(msg)
    return True


class EmailChannel(DeliveryChannel):
    """Delivers notifications via email using the injected `sender` or `smtp_send`."""

    name = "email"

    def deliver(self, payload: NotificationPayload, sender: SenderFn | None = None) -> bool:
        send = sender or smtp_send
        return bool(payload.recipients and send(payload.recipients, payload.subject, payload.body))


class LogChannel(DeliveryChannel):
    """Audit / dry-run channel that validates payload content without network calls."""

    name = "log"

    def deliver(self, payload: NotificationPayload, sender: SenderFn | None = None) -> bool:
        return bool(payload.subject and payload.body)


# Global registry of delivery channel strategies
CHANNELS: dict[str, DeliveryChannel] = {c.name: c for c in (EmailChannel(), LogChannel())}


# =====================================================================
# 2. MESSAGE COMPOSER & OBSERVER HANDLER
# =====================================================================

def build_message(event: AlertEvent, doctor_emails: list[str]) -> NotificationPayload:
    """Composes the English notification subject and body for astronaut and doctors."""
    draft = event.alert
    sev = SEVERITY_EN.get(draft.severity, draft.severity.upper())
    mode = MODE_EN.get(draft.sampling_mode_triggered, draft.sampling_mode_triggered)
    name = event.astronaut.get("full_name", "Crew Member")

    recipients = list(dict.fromkeys(e for e in [event.astronaut.get("email", ""), *doctor_emails] if e))

    subject = f"[Astro-Health] Alert {sev} - {name} (Day {draft.mission_day})"
    body = (
        f"Astro-Health Clinical Monitoring Report\n"
        f"Crew member: {name}\n"
        f"Mission day: {draft.mission_day}\n"
        f"Category: {draft.alert_category} ({draft.notification_id})\n"
        f"Severity: {sev}\n"
        f"Reason: {draft.title}\n"
        f"Triggered sampling mode: {mode}\n\n"
        f"Action plan:\n{draft.action_plan_text}\n"
    )
    return NotificationPayload(event.alert_id, recipients, subject, body)


def send_alert_notification(
    event: AlertEvent,
    *,
    db_path: Path | str | None = None,
    sender: SenderFn | None = None,
    channel: str = "email",
) -> bool:
    """Handles an `AlertEvent` idempotently: sends once and marks `alerts.email_sent = 1`."""
    with sesion(db_path) as db:
        stored = db.alertas.obtener(event.alert_id)
        if stored is None or stored.get("email_sent"):
            return False
        doctors = [u["email"] for u in db.usuarios.por_rol(Rol.DOCTOR)]

    if not CHANNELS[channel].deliver(build_message(event, doctors), sender=sender):
        return False

    with sesion(db_path) as db:
        db.alertas.actualizar(event.alert_id, email_sent=1)
    return True


def subscribe_notifications(
    bus: EventBus = default_bus,
    *,
    db_path: Path | str | None = None,
    sender: SenderFn | None = None,
    channel: str = "email",
) -> Callable[[], None]:
    """Subscribes `send_alert_notification` to `bus` (Observer) and returns unsubscribe callable."""
    return bus.subscribe(
        lambda event: send_alert_notification(event, db_path=db_path, sender=sender, channel=channel)
    )
