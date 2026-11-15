"""Reusable visual widgets styled after the Figma design.

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
    "yellow": ("#F59E0B", "Precaución"),
    "orange": ("#EA580C", "Riesgo Alto"),
    "red": ("#DC2626", "Crítico"),
}


# =====================================================================
# 1. GLOBAL FIGMA CSS STYLES
# =====================================================================

def aplicar_estilos_figma() -> None:
    """Injects Figma CSS rules (#577C8E sidebar, #FF9362 accordions/buttons, #D9D9D9 panels)."""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { background-color: #577C8E !important; }
        [data-testid="stSidebar"] * { color: #FFFFFF !important; }
        h1, h2, h3 { color: #233746 !important; font-family: "Georgia", serif !important; }
        div.stButton > button[kind="primary"], div.stFormSubmitButton > button {
            background-color: #FF9362 !important;
            color: #FFFFFF !important;
            border: 1px solid #C9643B !important;
            border-radius: 6px !important;
            font-family: "Georgia", serif !important;
        }
        div[data-testid="stExpander"] details summary {
            background-color: #FF9362 !important;
            color: #111111 !important;
            border-radius: 5px !important;
            font-family: "Georgia", serif !important;
            font-weight: 600 !important;
        }
        div[data-testid="stExpander"] details {
            border: none !important;
            background-color: #D9D9D9 !important;
            border-radius: 6px !important;
            margin-bottom: 10px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =====================================================================
# 2. CORE CLINICAL WIDGETS (Traffic light, KPI card, Readiness gauge, Alert card)
# =====================================================================

def semaforo(estado: str, etiqueta: str) -> None:
    """Renders a clinical traffic-light pill badge ('green' | 'yellow' | 'red')."""
    key = str(estado).lower()
    color, status_text = STATUS_LABELS.get(key, ("#577C8E", estado))
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:10px; background:#D9D9D9;
                    padding:8px 14px; border-radius:8px; border-left:6px solid {color}; margin-bottom:8px;">
            <span style="height:14px; width:14px; background:{color}; border-radius:50%; display:inline-block;"></span>
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
    valor: float | int | str,
    unidad: str = "",
    estado: str = "normal",
) -> None:
    """Renders a Figma-styled KPI indicator card with #D9D9D9 background and status accent border."""
    color = FIGMA_COLORS.get(str(estado).lower(), FIGMA_COLORS["normal"])
    val_str = f"{valor:.1f}" if isinstance(valor, float) else str(valor)
    unit_html = f'<span style="font-size:14px; color:#555555;"> {unidad}</span>' if unidad else ""
    st.markdown(
        f"""
        <div style="background:#D9D9D9; border-radius:8px; padding:12px 16px;
                    border-top:4px solid {color}; margin-bottom:10px;">
            <div style="font-size:12px; color:#555555; font-family:sans-serif;">{titulo}</div>
            <div style="font-size:24px; font-weight:700; color:#233746; font-family:'Georgia',serif;">
                {val_str}{unit_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def medidor_puntaje(puntaje: float, titulo: str = "Puntaje de Aptitud") -> None:
    """Renders a 0-100 Plotly gauge meter for the Daily Operational Readiness Score."""
    score = max(0.0, min(100.0, float(puntaje)))
    bar_color = "#2E7D32" if score >= 75 else ("#F59E0B" if score >= 50 else "#DC2626")

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=score,
            title={"text": titulo, "font": {"size": 16, "family": "Georgia", "color": "#233746"}},
            number={"suffix": " / 100", "font": {"size": 24, "family": "Georgia", "color": "#233746"}},
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
    fig.update_layout(height=210, margin={"l": 20, "r": 20, "t": 40, "b": 10}, paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)


def tarjeta_alerta(alerta: Mapping[str, Any]) -> None:
    """Renders an alert card with its severity badge, metadata, and medical action plan."""
    sev = str(alerta.get("severity", "yellow")).lower()
    color = FIGMA_COLORS.get(sev, FIGMA_COLORS["yellow"])
    badge = "ALERTA ROJA" if sev == "red" else "ALERTA AMARILLA"
    rules_json = alerta.get("triggered_rules_json")
    notif_id = rules_json.get("notification_id") if isinstance(rules_json, Mapping) else alerta.get("alert_category", "")

    st.markdown(
        f"""
        <div style="background:#FFFFFF; border:1px solid #D9D9D9; border-left:6px solid {color};
                    border-radius:8px; padding:14px 18px; margin-bottom:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="background:{color}; color:#FFFFFF; font-size:11px; font-weight:700;
                             padding:3px 8px; border-radius:4px; font-family:sans-serif;">
                    {badge} · {notif_id}
                </span>
                <span style="font-size:12px; color:#555555; font-family:sans-serif;">
                    Día de misión {alerta.get("mission_day", "-")} · Modo: {alerta.get("sampling_mode_triggered", "standard")}
                </span>
            </div>
            <div style="font-size:18px; font-weight:600; color:#233746; margin:8px 0 4px 0; font-family:'Georgia',serif;">
                {alerta.get("title", "Alerta clínica")}
            </div>
            <div style="font-size:14px; color:#333333; background:#D9D9D9; padding:10px; border-radius:6px; font-family:sans-serif;">
                <strong>Plan de acción:</strong> {alerta.get("action_plan_text", "Consultar al médico de vuelo.")}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =====================================================================
# 3. FIGMA PATIENT HEADER, TABLE HEADER & ITEM CARDS
# =====================================================================

def ficha_paciente(paciente: Mapping[str, Any], *, estilo_azul: bool = False) -> None:
    """Renders the 4 top Figma boxes: Name, Last name, Sex, Birth date (#D9D9D9 or #C7D9E5)."""
    full = str(paciente.get("full_name", "")).strip().split(" ", 1)
    first_name = paciente.get("first_name") or (full[0] if full else "")
    last_name = paciente.get("last_name") or (full[1] if len(full) > 1 else "")
    sex_raw = paciente.get("sex", 0)
    sex_txt = "Female" if sex_raw in (0, "F", "Female", "female") else "Male"
    bg = FIGMA_COLORS["table_blue"] if estilo_azul else FIGMA_COLORS["neutral_grey"]

    cols = st.columns([1.2, 1.8, 1.0, 1.4])
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
    proporciones: Sequence[float] | None = None,
) -> None:
    """Renders the #C7D9E5 light-blue table header bar from `Lista astronautas.png`."""
    cols = st.columns(proporciones or [1] * len(columnas))
    for col, text in zip(cols, columnas):
        col.markdown(
            f'<div style="background:#C7D9E5; border-radius:5px; padding:10px 14px; '
            f'font-family:Georgia,serif; font-weight:600; color:#111111; margin-bottom:8px;">{text}</div>',
            unsafe_allow_html=True,
        )


def tarjeta_item(
    nombre: str,
    detalle: str = "",
    *,
    key_prefix: str = "item",
) -> tuple[bool, bool]:
    """Renders a white card with #D9D9D9 Name box for a Pill or Meal (`Pills_detail.png`)."""
    with st.container(border=True):
        st.markdown(
            f'<div style="background:#D9D9D9; border-radius:5px; padding:6px 12px; max-width:240px; margin-bottom:6px;">'
            f'<div style="font-size:11px; color:#555555; font-family:sans-serif;">Name</div>'
            f'<div style="font-size:16px; color:#111111; font-family:Georgia,serif;">{nombre}</div></div>',
            unsafe_allow_html=True,
        )
        if detalle:
            st.caption(detalle)
        c_del, _, c_upd = st.columns([1, 5, 2])
        delete_clicked = c_del.button("🗑️", key=f"del_{key_prefix}")
        update_clicked = c_upd.button("Update", key=f"upd_{key_prefix}", use_container_width=True)
    return delete_clicked, update_clicked
