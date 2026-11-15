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
        title="Body mass loss",
        steps_yellow=(
            "Compare daily caloric intake against resting expenditure (Mifflin-St Jeor) and cover deficit.",
            "Review protein intake per meal with the flight nutritionist.",
            "Confirm adherence to scheduled resistance exercise.",
            "Repeat bioimpedance chair measurement at the next weekly check.",
        ),
        steps_red=(
            "Notify the flight surgeon and nutritionist today.",
            "Adjust caloric and protein plan with the nutritionist.",
            "Log all meals and percentage consumed.",
            "Measure body composition more frequently (alert_atrophy mode).",
        ),
        recipients_yellow=_C_TEAM,
        recipients_red=_C_TEAM,
        infographic_asset="c1_perdida_masa.png",
    ),
    "C2": ActionPlan(
        notification_id="C2",
        title="Low skeletal muscle index",
        steps_yellow=(
            "Prioritize scheduled daily resistance exercise sessions.",
            "Distribute protein intake across all meals, not only dinner.",
            "Repeat bioimpedance chair measurement at the next weekly check.",
        ),
        steps_red=(
            "Notify the flight surgeon and nutritionist today.",
            "Review resistance exercise load with the flight surgeon.",
            "Adjust protein supplementation plan with the nutritionist.",
            "Measure body composition more frequently (alert_atrophy mode).",
        ),
        recipients_yellow=_C_TEAM,
        recipients_red=_C_TEAM,
        infographic_asset="c2_musculo.png",
    ),
    "C3": ActionPlan(
        notification_id="C3",
        title="Elevated visceral fat",
        steps_yellow=(
            "Review balance between caloric intake and daily energy expenditure.",
            "Complete scheduled aerobic exercise sessions.",
            "Repeat bioimpedance chair measurement at the next weekly check.",
        ),
        steps_red=(
            "Notify the flight surgeon and nutritionist.",
            "Jointly review aerobic and resistance exercise plans.",
            "If muscle mass is also low, manage as sarcopenic obesity with the flight surgeon.",
        ),
        recipients_yellow=_C_TEAM,
        recipients_red=_C_TEAM,
        infographic_asset="c3_grasa_visceral.png",
    ),
    # --- Problem F: circadian rhythm, sleep and HPA axis -----------------------
    "F1": ActionPlan(
        notification_id="F1",
        title="Circadian misalignment",
        steps_yellow=(
            "Maintain fixed sleep and wake schedules.",
            "Use bright blue-enriched light upon awakening and dim light 1 hour before sleep.",
            "Avoid screens and high-workload tasks before bedtime.",
            "Log subjective alertness upon awakening over the next days.",
        ),
        steps_red=(
            "Notify the flight surgeon and flight psychologist.",
            "Request the commander to review workload and shift scheduling.",
            "Apply the light exposure and sleep hygiene protocol from the yellow plan.",
            "Collect additional salivary biomarker samples (alert_circadian mode).",
        ),
        recipients_yellow=("astronaut", "flight_surgeon", "psychologist"),
        recipients_red=("astronaut", "flight_surgeon", "psychologist", "commander"),
        infographic_asset="f1_circadiano.png",
    ),
    # --- Problem G: early infection -----------------------------------------------
    "G1": ActionPlan(
        notification_id="G1",
        title="Suspected infection",
        steps_yellow=(
            "Measure core body temperature with a clinical thermometer and log it.",
            "Increase fluid intake and rest.",
            "Minimize close contact with the rest of the crew.",
            "Repeat vital sign measurements before sleep.",
        ),
        steps_red=(
            "Notify the flight surgeon immediately.",
            "Isolate in personal crew quarters and wear a mask in shared modules.",
            "Measure core temperature every 4 hours (alert_infection mode).",
            "Follow flight surgeon instructions strictly before using the medical kit.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_ALL_CREW,
        infographic_asset="g1_infeccion.png",
    ),
    # --- Safety (S1-S4) ------------------------------------------------------------
    "S1": ActionPlan(
        notification_id="S1",
        title="Out-of-range blood glucose",
        steps_yellow=(
            "If low (< 70 mg/dL): ingest 15 g of fast-acting carbohydrates and recheck in 15 min.",
            "If high (> 180 mg/dL): hydrate with water and recheck in 2 hours.",
            "Log the meal and physical activity preceding the reading.",
        ),
        steps_red=(
            "If severely low (< 54 mg/dL): ingest 15 g of fast-acting carbohydrates "
            "immediately and recheck in 15 min.",
            "If severely high (> 250 mg/dL): hydrate and recheck in 1 hour.",
            "Notify the flight surgeon immediately.",
            "Do not perform critical tasks or EVA until glucose normalizes.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_BASIC,
        infographic_asset="s1_glucosa.png",
    ),
    "S2": ActionPlan(
        notification_id="S2",
        title="Elevated blood pressure",
        steps_yellow=(
            "Rest seated for 5 minutes and repeat the measurement.",
            "Log the value and any preceding physical activity.",
        ),
        steps_red=(
            "Stop current physical activity and rest immediately.",
            "Notify the flight surgeon immediately.",
            "Repeat blood pressure measurements every 5 minutes until instructed.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_BASIC,
        infographic_asset="s2_presion.png",
    ),
    "S3": ActionPlan(
        notification_id="S3",
        title="Low oxygen saturation",
        steps_yellow=(
            "Check optical sensor placement and repeat the reading.",
            "Rest and breathe calmly for 5 minutes.",
        ),
        steps_red=(
            "Notify the flight surgeon immediately.",
            "If SpO2 dropped in 2 or more crew members, check cabin life support (O2 and CO2).",
            "Follow supplemental oxygen protocol as directed by the flight surgeon.",
        ),
        recipients_yellow=_BASIC,
        recipients_red=_ALL_CREW,
        infographic_asset="s3_spo2.png",
    ),
    "S4": ActionPlan(
        notification_id="S4",
        title="Radiation exposure alert",
        steps_yellow=(
            "Cumulative dose exceeded 50% of the mission limit.",
            "Review activities that increase exposure with the flight surgeon.",
            "Verify personal dosimeter readings.",
        ),
        steps_red=(
            "Active solar particle event: move immediately to the heavily shielded storm shelter.",
            "Suspend or postpone any extravehicular activity (EVA).",
            "Remain in the radiation shelter until proton flux drops below 10 pfu.",
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
