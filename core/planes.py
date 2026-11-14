"""Action plans per notification ID (C1-C3, F1, G1, S1-S4).

Each plan holds the title, the steps by severity, and who must be notified,
following the "Notificaciones" table of the team document.

Pattern: Registry. Plans are data, not code. Adding or editing a plan only means
changing ``ACTION_PLANS``; ``core/alertas.py`` looks them up by notification ID.

DRAFT CONTENT: the steps are general countermeasures and must be reviewed by the
team before the demo. They never prescribe specific drugs or doses: anything
pharmacological is decided by the flight surgeon.

Architecture rule: Importing streamlit or sqlite3 inside core/ is strictly forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PlanSeverity = Literal["yellow", "red"]
Recipient = Literal[
    "astronaut",
    "flight_surgeon",
    "nutritionist",
    "psychologist",
    "commander",
    "crew",
]


@dataclass(frozen=True)
class ActionPlan:
    """Action plan for one notification ID.

    Attributes:
        notification_id: ID used in ``TriggeredRule.notification_id`` (e.g. "G1").
        title: Short title shown on the alert card.
        steps_yellow: Steps for a yellow alert.
        steps_red: Steps for a red alert (usually yellow steps plus escalation).
        recipients_yellow: Who is notified on yellow.
        recipients_red: Who is notified on red.
        infographic_asset: Optional image under ``assets/infografias/``.
    """

    notification_id: str
    title: str
    steps_yellow: tuple[str, ...]
    steps_red: tuple[str, ...]
    recipients_yellow: tuple[Recipient, ...]
    recipients_red: tuple[Recipient, ...]
    infographic_asset: str | None = None

    def steps(self, severity: PlanSeverity) -> tuple[str, ...]:
        """Returns the steps that apply to a severity."""
        return self.steps_red if severity == "red" else self.steps_yellow

    def recipients(self, severity: PlanSeverity) -> tuple[Recipient, ...]:
        """Returns who must be notified for a severity."""
        return self.recipients_red if severity == "red" else self.recipients_yellow

    def render(self, severity: PlanSeverity) -> str:
        """Builds the numbered plan text stored in ``alerts.action_plan_text``."""
        return "\n".join(f"{i}. {step}" for i, step in enumerate(self.steps(severity), start=1))


_C_TEAM: tuple[Recipient, ...] = ("astronaut", "flight_surgeon", "nutritionist")
_BASIC: tuple[Recipient, ...] = ("astronaut", "flight_surgeon")
_ALL_CREW: tuple[Recipient, ...] = ("astronaut", "flight_surgeon", "commander", "crew")


ACTION_PLANS: dict[str, ActionPlan] = {
    # --- Problem C: muscle, malnutrition and visceral fat -----------------------
    "C1": ActionPlan(
        notification_id="C1",
        title="Pérdida de masa corporal",
        steps_yellow=(
            "Comparar la ingesta diaria con el gasto en reposo (Mifflin-St Jeor) y cubrir el déficit.",
            "Revisar con el nutricionista el aporte de proteína de cada comida.",
            "Confirmar la adherencia al ejercicio de resistencia programado.",
            "Repetir la medición en la silla en la próxima toma semanal.",
        ),
        steps_red=(
            "Avisar al médico de vuelo y al nutricionista hoy mismo.",
            "Ajustar el plan calórico y proteico con el nutricionista.",
            "Registrar todas las comidas y su porcentaje consumido.",
            "Medir composición corporal con mayor frecuencia (modo alert_atrophy).",
        ),
        recipients_yellow=_C_TEAM,
        recipients_red=_C_TEAM,
        infographic_asset="c1_perdida_masa.png",
    ),
    "C2": ActionPlan(
        notification_id="C2",
        title="Índice de músculo bajo",
        steps_yellow=(
            "Priorizar las sesiones de ejercicio de resistencia del día.",
            "Distribuir la proteína en todas las comidas, no solo en la cena.",
            "Repetir la medición en la silla en la próxima toma semanal.",
        ),
        steps_red=(
            "Avisar al médico de vuelo y al nutricionista hoy mismo.",
            "Revisar con el médico la carga de ejercicio de resistencia.",
            "Ajustar el plan proteico con el nutricionista.",
            "Medir composición corporal con mayor frecuencia (modo alert_atrophy).",
        ),
        recipients_yellow=_C_TEAM,
        recipients_red=_C_TEAM,
        infographic_asset="c2_musculo.png",
    ),
    "C3": ActionPlan(
        notification_id="C3",
        title="Grasa visceral elevada",
        steps_yellow=(
            "Revisar el balance entre calorías consumidas y gasto diario.",
            "Completar el ejercicio aeróbico programado.",
            "Repetir la medición en la silla en la próxima toma semanal.",
        ),
        steps_red=(
            "Avisar al médico de vuelo y al nutricionista.",
            "Revisar el plan de ejercicio aeróbico y de resistencia en conjunto.",
            "Si también hay músculo bajo, tratarlo como obesidad sarcopénica con el médico.",
        ),
        recipients_yellow=_C_TEAM,
        recipients_red=_C_TEAM,
        infographic_asset="c3_grasa_visceral.png",
    ),
    # --- Problem F: circadian rhythm, sleep and HPA axis -----------------------
    "F1": ActionPlan(
        notification_id="F1",
        title="Desalineación circadiana",
        steps_yellow=(
            "Mantener horarios fijos para dormir y despertar.",
            "Usar luz intensa al despertar y luz tenue en la hora previa a dormir.",
            "Evitar pantallas y tareas exigentes antes de dormir.",
            "Registrar cómo se siente al despertar durante los próximos días.",
        ),
        steps_red=(
            "Avisar al médico de vuelo y al psicólogo de vuelo.",
            "Pedir al comandante que revise la carga y el horario de trabajo.",
            "Aplicar el plan de luz y de horario de los pasos amarillos.",
            "Tomar muestras de saliva adicionales (modo alert_circadian).",
        ),
        recipients_yellow=("astronaut", "flight_surgeon", "psychologist"),
        recipients_red=("astronaut", "flight_surgeon", "psychologist", "commander"),
        infographic_asset="f1_circadiano.png",
    ),
    # --- Problem G: early infection -----------------------------------------------
    "G1": ActionPlan(
        notification_id="G1",
        title="Sospecha de infección",
        steps_yellow=(
            "Medir la temperatura central con termómetro y registrarla.",
            "Aumentar la hidratación y el descanso.",
            "Reducir el contacto cercano con el resto de la tripulación.",
            "Repetir la medición de signos vitales en la noche.",
        ),
        steps_red=(
            "Avisar de inmediato al médico de vuelo.",
            "Aislarse en su módulo y usar mascarilla en áreas comunes.",
            "Medir temperatura central cada 4 horas (modo alert_infection).",
            "Seguir solo las indicaciones del médico sobre el kit médico.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_ALL_CREW,
        infographic_asset="g1_infeccion.png",
    ),
    # --- Safety (S1-S4) ------------------------------------------------------------
    "S1": ActionPlan(
        notification_id="S1",
        title="Glucosa fuera de rango",
        steps_yellow=(
            "Si está baja (< 70 mg/dL): tomar 15 g de carbohidratos de acción rápida "
            "y volver a medir en 15 min.",
            "Si está alta (> 180 mg/dL): hidratarse y volver a medir en 2 horas.",
            "Registrar la comida y la actividad previas a la medición.",
        ),
        steps_red=(
            "Si está muy baja (< 54 mg/dL): tomar 15 g de carbohidratos de acción rápida "
            "de inmediato y volver a medir en 15 min.",
            "Si está muy alta (> 250 mg/dL): hidratarse y volver a medir en 1 hora.",
            "Avisar al médico de vuelo.",
            "No realizar tareas críticas ni actividad extravehicular hasta normalizar.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_BASIC,
        infographic_asset="s1_glucosa.png",
    ),
    "S2": ActionPlan(
        notification_id="S2",
        title="Presión arterial elevada",
        steps_yellow=(
            "Reposar sentado 5 minutos y volver a medir.",
            "Registrar el valor y la actividad previa.",
        ),
        steps_red=(
            "Suspender la actividad y reposar.",
            "Avisar de inmediato al médico de vuelo.",
            "Volver a medir cada 5 minutos hasta recibir indicaciones.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_BASIC,
        infographic_asset="s2_presion.png",
    ),
    "S3": ActionPlan(
        notification_id="S3",
        title="Saturación de oxígeno baja",
        steps_yellow=(
            "Revisar la colocación del sensor y volver a medir.",
            "Reposar y respirar con calma durante 5 minutos.",
        ),
        steps_red=(
            "Avisar de inmediato al médico de vuelo.",
            "Si bajó en 2 o más tripulantes, revisar el soporte vital de la cabina (O₂ y CO₂).",
            "Seguir el protocolo de oxígeno suplementario que indique el médico.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_ALL_CREW,
        infographic_asset="s3_spo2.png",
    ),
    "S4": ActionPlan(
        notification_id="S4",
        title="Radiación",
        steps_yellow=(
            "La dosis acumulada superó el 50 % del límite de la misión.",
            "Revisar con el médico las actividades que aumentan la exposición.",
            "Verificar el dosímetro personal.",
        ),
        steps_red=(
            "Evento solar activo: ir al área más blindada del vehículo (refugio).",
            "Suspender o posponer cualquier actividad extravehicular.",
            "Permanecer en el refugio hasta que el flujo de protones baje de 10 pfu.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_ALL_CREW,
        infographic_asset="s4_radiacion.png",
    ),
}


def get_action_plan(notification_id: str) -> ActionPlan:
    """Returns the plan registered for a notification ID.

    Raises:
        KeyError: If the ID has no registered plan.
    """
    try:
        return ACTION_PLANS[notification_id]
    except KeyError as exc:
        raise KeyError(f"No action plan registered for notification '{notification_id}'.") from exc
