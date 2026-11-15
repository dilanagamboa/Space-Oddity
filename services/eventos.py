"""In-process event bus (Observer pattern).

Decouples who *produces* an alert (``services/reportes.py``) from who *reacts* to it
(``services/notificaciones.py``, a future Slack channel, an on-screen banner...).
The producer never imports the consumers.

Contract for consumers::

    from services.eventos import AlertEvent, subscribe

    def send_email(event: AlertEvent) -> None:
        ...

    subscribe(send_email)

Rules:
    * Events are published **after** the database transaction commits, so a
      consumer never reacts to an alert that was rolled back.
    * A consumer that raises does not stop the others nor the pipeline: the
      error is logged and the next handler runs.

Architecture rule: no streamlit imports here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from core.alertas import AlertDraft

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertEvent:
    """An alert that was just stored.

    Attributes:
        alert_id: ``alerts.id`` of the stored row.
        id_mission_log: Mission log the alert belongs to.
        astronaut: ``{"id", "full_name", "email"}`` of the astronaut.
        alert: Full alert (severity, plan, recipients, triggered rules...).
        is_simulated: ``True`` if it comes from a doctor's simulation.
    """

    alert_id: int
    id_mission_log: int
    astronaut: Mapping[str, Any]
    alert: AlertDraft
    is_simulated: bool = False

    @property
    def is_immediate(self) -> bool:
        """Red alerts must be sent right away (team notification rules)."""
        return self.alert.is_immediate


AlertHandler = Callable[[AlertEvent], None]


@dataclass
class EventBus:
    """Minimal synchronous publish/subscribe bus."""

    _handlers: list[AlertHandler] = field(default_factory=list)

    def subscribe(self, handler: AlertHandler) -> Callable[[], None]:
        """Registers a handler. Returns a function that unsubscribes it."""
        if handler not in self._handlers:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    def publish(self, event: AlertEvent) -> int:
        """Sends the event to every handler. Returns how many handled it without errors."""
        delivered = 0
        for handler in list(self._handlers):
            try:
                handler(event)
                delivered += 1
            except Exception:  # A broken consumer must never break the pipeline
                logger.exception("Alert handler %r failed for alert %s", handler, event.alert_id)
        return delivered

    def clear(self) -> None:
        """Removes every handler (useful in tests)."""
        self._handlers.clear()


default_bus = EventBus()
"""Bus shared by the whole app."""


def subscribe(handler: AlertHandler) -> Callable[[], None]:
    """Registers a handler on the default bus."""
    return default_bus.subscribe(handler)


def publish_alert(event: AlertEvent) -> int:
    """Publishes an alert on the default bus."""
    return default_bus.publish(event)
