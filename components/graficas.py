"""Reusable Plotly chart builders styled after the Figma palette (#577C8E, #FF9362, #C7D9E5, #D9D9D9).

Contract:
    * Functions receive plain sequences of dictionaries (`Sequence[Mapping[str, Any]]`).
    * Functions return a `plotly.graph_objects.Figure` (and never query the database).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import plotly.graph_objects as go


def _base_layout(fig: go.Figure, titulo: str, unidad_y: str = "") -> go.Figure:
    """Applies the shared Figma light-theme typography and #D9D9D9 gridlines to a Plotly figure."""
    fig.update_layout(
        title={"text": titulo, "font": {"family": "Georgia, serif", "size": 18, "color": "#233746"}},
        xaxis={"title": "Día de misión", "gridcolor": "#D9D9D9"},
        yaxis={"title": unidad_y, "gridcolor": "#D9D9D9"},
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="rgba(0,0,0,0)",
        font={"family": "sans-serif", "color": "#233746"},
        margin={"l": 40, "r": 20, "t": 50, "b": 40},
        height=320,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
    )
    return fig


def grafica_tendencia(
    datos: Sequence[Mapping[str, Any]],
    eje_x: str,
    eje_y: str,
    titulo: str,
    *,
    unidad: str = "",
    rango_normal: tuple[float, float] | None = None,
    umbral_amarillo: float | None = None,
    umbral_rojo: float | None = None,
) -> go.Figure:
    """Builds a mission trend line chart using #577C8E line and #FF9362 markers."""
    xs = [row[eje_x] for row in datos if row.get(eje_x) is not None and row.get(eje_y) is not None]
    ys = [float(row[eje_y]) for row in datos if row.get(eje_x) is not None and row.get(eje_y) is not None]

    fig = go.Figure()

    if rango_normal is not None:
        fig.add_hrect(
            y0=rango_normal[0],
            y1=rango_normal[1],
            fillcolor="#C7D9E5",
            opacity=0.35,
            line_width=0,
            annotation_text="Rango normal",
            annotation_position="top left",
        )
    if umbral_amarillo is not None:
        fig.add_hline(y=umbral_amarillo, line_dash="dash", line_color="#F59E0B", annotation_text="Alerta amarilla")
    if umbral_rojo is not None:
        fig.add_hline(y=umbral_rojo, line_dash="dot", line_color="#DC2626", annotation_text="Alerta roja")

    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines+markers",
            name=titulo,
            line={"color": "#577C8E", "width": 2.5},
            marker={"color": "#FF9362", "size": 6},
        )
    )
    return _base_layout(fig, titulo, unidad)


def grafica_curva_cortisol(
    muestras: Sequence[Mapping[str, Any]],
    titulo: str = "Curva Diurna de Cortisol Salival (CAR)",
) -> go.Figure:
    """Builds the 4-point diurnal cortisol curve using #FF9362 coral line."""
    slot_labels = {
        "wake_0m": "Despertar (0m)",
        "wake_30m": "+30 min (CAR)",
        "afternoon": "Tarde (16:00)",
        "bedtime": "Noche (23:00)",
    }
    by_slot = {str(r.get("collection_slot")): r for r in muestras}
    ordered_slots = [s for s in slot_labels if s in by_slot]

    xs = [slot_labels[s] for s in ordered_slots]
    ys = [float(by_slot[s]["salivary_cortisol_nmol_l"]) for s in ordered_slots]

    fig = go.Figure(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines+markers",
            name="Cortisol (nmol/L)",
            line={"color": "#FF9362", "width": 3},
            marker={"color": "#233746", "size": 8},
            fill="tozeroy",
            fillcolor="rgba(255, 147, 98, 0.18)",
        )
    )
    fig = _base_layout(fig, titulo, "nmol/L")
    fig.update_layout(xaxis_title="Toma salival")
    return fig


def grafica_sueno_apilada(
    datos: Sequence[Mapping[str, Any]],
    titulo: str = "Arquitectura del Sueño (Horas por etapa)",
) -> go.Figure:
    """Builds a stacked bar chart of sleep stages using #233746, #577C8E, and #C7D9E5."""
    days = [r["mission_day"] for r in datos]
    stages = (
        ("sleep_deep_min", "Sueño Profundo", "#233746"),
        ("sleep_rem_min", "Sueño REM", "#577C8E"),
        ("sleep_light_min", "Sueño Ligero", "#C7D9E5"),
    )

    fig = go.Figure()
    for col, label, color in stages:
        hours = [round(float(r.get(col) or 0.0) / 60.0, 2) for r in datos]
        fig.add_trace(go.Bar(x=days, y=hours, name=label, marker_color=color))

    fig.add_hline(y=7.0, line_dash="dash", line_color="#2E7D32", annotation_text="Meta AASM (7 h)")
    fig = _base_layout(fig, titulo, "Horas (h)")
    fig.update_layout(barmode="stack")
    return fig
