"""Reusable visual widgets styled after the Figma design (English UI).

Contract:
    * Widgets receive plain Python types (dicts, lists, strings, numbers).
    * Widgets NEVER query the database or import `services/database.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import plotly.graph_objects as go
import streamlit as st

# Exact HEX palette from Figma
FIGMA_COLORS: dict[str, str] = {
    "sidebar_slate": "#577C8E",
    "table_blue": "#C7D9E5",
    "coral_orange": "#FF9362",
    "dark_navy": "#233746",
    "neutral_grey": "#D9D9D9",
    "green": "#2E7D32",
    "yellow": "#F59E0B",
    "orange": "#EA580C",
    "red": "#DC2626",
    "normal": "#577C8E",
}

STATUS_LABELS: dict[str, tuple[str, str]] = {
    "green": ("#2E7D32", "Nominal"),
    "normal": ("#577C8E", "Nominal"),
    "yellow": ("#F59E0B", "Caution"),
    "orange": ("#EA580C", "High Risk"),
    "red": ("#DC2626", "Critical"),
}


# =====================================================================
# 1. CORE CLINICAL WIDGETS (Traffic light, KPI card, Readiness gauge, Alert card)
# =====================================================================

def semaforo(estado: str | None, etiqueta: str = "Triage") -> None:
    """Renders a clinical traffic-light pill badge ('green' | 'yellow' | 'red')."""
    key = str(estado or "normal").lower()
    color, status_text = STATUS_LABELS.get(key, ("#577C8E", str(estado or "—")))
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:10px; background:#D9D9D9;
                    padding:10px 14px; border-radius:8px; border-left:6px solid {color}; margin-bottom:8px;">
            <span style="height:14px; width:14px; background:{color}; border-radius:50%;
                         display:inline-block;"></span>
            <div style="font-family:sans-serif;">
                <div style="font-size:12px; color:#555555;">{etiqueta}</div>
                <div style="font-size:15px; font-weight:700; color:#233746;">{status_text}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def tarjeta_kpi(
    titulo: str,
    valor: float | int | str | None,
    unidad: str = "",
    estado: str = "normal",
) -> None:
    """Renders a Figma-styled KPI indicator card with #D9D9D9 background and status accent border."""
    color = FIGMA_COLORS.get(str(estado).lower(), FIGMA_COLORS["normal"])
    if valor is None:
        val_str = "—"
    elif isinstance(valor, float):
        val_str = f"{valor:.1f}"
    else:
        val_str = str(valor)
    unit_html = (
        f'<span style="font-size:14px; color:#555555;"> {unidad}</span>'
        if unidad and valor is not None
        else ""
    )
    st.markdown(
        f"""
        <div style="background:#D9D9D9; border-radius:8px; padding:10px 14px;
                    border-top:4px solid {color}; margin-bottom:10px;">
            <div style="font-size:12px; color:#555555; font-family:sans-serif;">{titulo}</div>
            <div style="font-size:22px; font-weight:700; color:#233746; font-family:'Georgia',serif;">
                {val_str}{unit_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def medidor_puntaje(puntaje: float | None, titulo: str = "Readiness Score") -> None:
    """Renders a 0-100 Plotly gauge meter for the Daily Operational Readiness Score."""
    score = max(0.0, min(100.0, float(puntaje or 0.0)))
    bar_color = "#2E7D32" if score >= 75 else ("#F59E0B" if score >= 50 else "#DC2626")

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=score,
            title={"text": titulo, "font": {"size": 15, "family": "Georgia", "color": "#233746"}},
            number={"suffix": " / 100", "font": {"size": 22, "family": "Georgia", "color": "#233746"}},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1},
                "bar": {"color": bar_color},
                "bgcolor": "#D9D9D9",
                "steps": [
                    {"range": [0, 50], "color": "#FEE2E2"},
                    {"range": [50, 75], "color": "#FEF3C7"},
                    {"range": [75, 100], "color": "#DCFCE7"},
                ],
            },
        )
    )
    fig.update_layout(height=195, margin={"l": 16, "r": 16, "t": 36, "b": 10}, paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)


def tarjeta_alerta(alerta: Mapping[str, Any]) -> None:
    """Renders an alert card with its severity badge, metadata, and medical Action Plan."""
    sev = str(alerta.get("severity", "yellow")).lower()
    color = FIGMA_COLORS.get(sev, FIGMA_COLORS["yellow"])
    badge = "RED ALERT" if sev == "red" else ("ORANGE ALERT" if sev == "orange" else "YELLOW ALERT")
    rules_json = alerta.get("triggered_rules_json")
    notif_id = (
        rules_json.get("notification_id")
        if isinstance(rules_json, Mapping)
        else alerta.get("alert_category", "")
    )
    sim_label = " · SIMULATED" if alerta.get("is_simulated") else ""
    day = alerta.get("mission_day", "-")
    mode = alerta.get("sampling_mode_triggered") or "standard"
    status = alerta.get("status", "open")

    st.markdown(
        f"""
        <div style="background:#FFFFFF; border:1px solid #D9D9D9; border-left:6px solid {color};
                    border-radius:8px; padding:14px 18px; margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="background:{color}; color:#FFFFFF; font-size:11px; font-weight:700;
                             padding:3px 8px; border-radius:4px; font-family:sans-serif;">
                    {badge} · {notif_id}{sim_label}
                </span>
                <span style="font-size:12px; color:#555555; font-family:sans-serif;">
                    Day {day} · Mode: {mode} · Status: {status}
                </span>
            </div>
            <div style="font-size:18px; font-weight:600; color:#233746; margin:8px 0 4px 0;
                        font-family:'Georgia',serif;">
                {alerta.get("title", "Clinical alert")}
            </div>
            <div style="font-size:14px; color:#233746; background:#D9D9D9; padding:10px 12px;
                        border-radius:6px; font-family:sans-serif; white-space:pre-line;">
                <strong>Action plan:</strong>\n{alerta.get("action_plan_text", "Consult flight surgeon.")}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =====================================================================
# 2. FIGMA PATIENT HEADER, TABLE HEADER, ITEM CARDS & CYCLE TAGS
# =====================================================================

def ficha_paciente(paciente: Mapping[str, Any], *, estilo_azul: bool = False) -> None:
    """Renders the 4 top Figma boxes: Name, Last name, Sex, Birth date (#D9D9D9 or #C7D9E5)."""
    full = str(paciente.get("full_name", "")).strip().split(" ", 1)
    first_name = paciente.get("name") or paciente.get("first_name") or (full[0] if full else "")
    last_name = paciente.get("last_name") or (full[1] if len(full) > 1 else "")
    sex_raw = paciente.get("sex", 0)
    sex_txt = "Female" if sex_raw in (0, "F", "Female", "female") else "Male"
    bg = FIGMA_COLORS["table_blue"] if estilo_azul else FIGMA_COLORS["neutral_grey"]

    cols = st.columns([2, 3, 2, 2])
    for col, label, val in (
        (cols[0], "Name", first_name),
        (cols[1], "Last name", last_name),
        (cols[2], "Sex", sex_txt),
        (cols[3], "Birth date", str(paciente.get("birth_date", "-"))),
    ):
        col.markdown(
            f'<div style="background:{bg}; border-radius:6px; padding:8px 14px; margin-bottom:12px;">'
            f'<div style="font-size:11px; color:#555555; font-family:sans-serif;">{label}</div>'
            f'<div style="font-size:18px; color:#111111; font-family:sans-serif;">{val}</div></div>',
            unsafe_allow_html=True,
        )


def encabezado_tabla(
    columnas: Sequence[str],
    proporciones: Sequence[int | float] | None = None,
) -> None:
    """Renders the #C7D9E5 light-blue table header bar from `Lista astronautas.png`."""
    with st.container(key="patients_header"):
        cols = st.columns(proporciones or [1] * len(columnas))
        for col, text in zip(cols, columnas, strict=True):
            col.markdown(f"**{text}**")


def tarjeta_item(
    nombre: str,
    detalle: str = "",
    *,
    key_prefix: str = "item",
    editable: bool = True,
) -> tuple[bool, bool]:
    """Renders a white card with #D9D9D9 Name box for a Pill, Meal or Notice (`Pills_detail.png`)."""
    with st.container(border=True, key=f"card_{key_prefix}"):
        st.markdown(
            f'<div style="background:#D9D9D9; border-radius:5px; padding:6px 12px; '
            f'max-width:260px; margin-bottom:6px;">'
            f'<div style="font-size:11px; color:#555555; font-family:sans-serif;">Name</div>'
            f'<div style="font-size:16px; color:#111111; font-family:Georgia,serif;">{nombre}</div></div>',
            unsafe_allow_html=True,
        )
        if detalle:
            st.caption(detalle)
        if not editable:
            return False, False
        c_del, _, c_upd = st.columns([1, 6, 2])
        # ESTO SE CAMBIA POR IMAGEN (basurero)
        delete_clicked = c_del.button("🗑️", key=f"del_{key_prefix}", help="Remove", type="tertiary")
        update_clicked = c_upd.button("Update", key=f"upd_{key_prefix}", width="stretch")
    return delete_clicked, update_clicked


def tarjeta_ciclo(registros: Sequence[Mapping[str, Any]]) -> None:
    """Renders menstrual cycle records as clean Figma cards instead of a provisional dataframe."""
    if not registros:
        st.caption("No menstrual cycle records available.")
        return
    latest = registros[0]
    c1, c2 = st.columns([1, 2])
    with c1:
        tarjeta_kpi("Current Phase", str(latest.get("cycle_phase", "—")).capitalize(), estado="normal")
    with c2:
        tarjeta_kpi("Last Recorded Date", str(latest.get("recorded_date", "—")), estado="normal")

    pills_html = "".join(
        f'<span style="display:inline-block; background:#D9D9D9; color:#233746; border-radius:6px; '
        f'padding:4px 10px; margin:3px; font-size:12px; font-family:sans-serif;">'
        f'<strong>{r.get("recorded_date")}</strong>: {r.get("cycle_phase")}</span>'
        for r in registros[:14]
    )
    st.markdown(f"<div style='margin-top:4px;'>{pills_html}</div>", unsafe_allow_html=True)
