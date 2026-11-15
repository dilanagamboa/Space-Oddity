"""Alert notification dispatcher using Strategy + Registry and Observer patterns."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
import smtplib
from typing import Any, ClassVar

from config import DB_PATH
from core.enums import Rol
from services.database import sesion
from services.eventos import AlertEvent, EventBus, default_bus

SenderFn = Callable[[list[str], str, str], bool]

SEVERITY_ES = {"red": "ROJA (Acción Inmediata)", "orange": "NARANJA", "yellow": "AMARILLA"}
MODE_ES = {
    "standard": "Estándar",
    "alert_infection": "Alerta de Infección / Sepsis",
    "alert_atrophy": "Alerta de Atrofia / Malnutrición",
    "alert_circadian": "Alerta Circadiana / Estrés HPA",
}


@dataclass(frozen=True)
class NotificationPayload:
    alert_id: int
    recipients: list[str]
    subject: str
    body: str
    sent_at: datetime


class DeliveryChannel(ABC):
    """Base strategy for a notification delivery channel."""

    name: ClassVar[str]

    @abstractmethod
    def deliver(self, payload: NotificationPayload, sender: SenderFn | None = None) -> bool:
        raise NotImplementedError


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
    """Sends an email via Gmail SMTP SSL (returns False if credentials are not configured)."""
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
    name = "email"

    def deliver(self, payload: NotificationPayload, sender: SenderFn | None = None) -> bool:
        if not payload.recipients:
            return False
        return bool((sender or smtp_send)(payload.recipients, payload.subject, payload.body))


class LogChannel(DeliveryChannel):
    name = "log"

    def deliver(self, payload: NotificationPayload, sender: SenderFn | None = None) -> bool:
        return bool(payload.subject and payload.body)


CHANNELS: dict[str, DeliveryChannel] = {c.name: c for c in (EmailChannel(), LogChannel())}


def build_message(
    event: AlertEvent,
    doctor_emails: list[str],
    *,
    now: datetime | None = None,
) -> NotificationPayload:
    """Formats the clinical alert notification in Spanish for the astronaut and flight surgeon."""
    stamp = now or datetime.now(UTC)
    draft = event.alert
    sev = SEVERITY_ES.get(draft.severity, draft.severity.upper())
    mode = MODE_ES.get(draft.sampling_mode_triggered, draft.sampling_mode_triggered)
    name = event.astronaut.get("full_name", "Tripulante")
    astro_email = event.astronaut.get("email", "")

    recipients = [e for e in [astro_email, *doctor_emails] if e]
    recipients = list(dict.fromkeys(recipients))

    subject = f"[Astro-Health] Alerta {sev} - {name} (Día {draft.mission_day})"
    body = (
        f"Reporte de Monitoreo Clínico Astro-Health\n"
        f"Tripulante: {name}\n"
        f"Día de misión: {draft.mission_day}\n"
        f"Categoría: {draft.alert_category} ({draft.notification_id})\n"
        f"Severidad: {sev}\n"
        f"Motivo: {draft.title}\n"
        f"Modo de muestreo activado: {mode}\n\n"
        f"Plan de acción:\n{draft.action_plan_text}\n"
    )
    return NotificationPayload(
        alert_id=event.alert_id, recipients=recipients, subject=subject, body=body, sent_at=stamp
    )


def send_alert_notification(
    event: AlertEvent,
    *,
    db_path: Path | str = DB_PATH,
    sender: SenderFn | None = None,
    channel: str = "email",
    now: datetime | None = None,
) -> bool:
    """Handles an `AlertEvent` idempotently: sends once and sets `alerts.email_sent = 1`."""
    with sesion(db_path) as db:
        stored = db.alertas.obtener(event.alert_id)
        if stored is None or stored.get("email_sent"):
            return False
        doctors = [u["email"] for u in db.usuarios.por_rol(Rol.DOCTOR)]

    payload = build_message(event, doctors, now=now)
    strategy = CHANNELS[channel]
    if not strategy.deliver(payload, sender=sender):
        return False

    with sesion(db_path) as db:
        db.alertas.actualizar(event.alert_id, email_sent=1)
    return True


def subscribe_notifications(
    bus: EventBus = default_bus,
    *,
    db_path: Path | str = DB_PATH,
    sender: SenderFn | None = None,
    channel: str = "email",
    now: datetime | None = None,
) -> Callable[[], None]:
    """Subscribes the notification handler to `bus` and returns the unsubscribe callable."""
    def _handler(event: AlertEvent) -> None:
        send_alert_notification(event, db_path=db_path, sender=sender, channel=channel, now=now)

    return bus.subscribe(_handler)
