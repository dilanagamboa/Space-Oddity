"""Patient screens shared by several pages: header, patient table and detail sections.

The doctor's list screens of the design (patients, pills, meals, notifications)
are the same table with a different action, so they share ``patient_picker``.
Containers with a ``key`` get a ``st-key-<key>`` CSS class used by assets/estilo.css.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from components.graficas import panel_tendencias
from components.navegacion import EDITING, current_user, go, is_doctor, open_patient
from components.widgets import (
    encabezado_tabla,
    ficha_paciente,
    medidor_puntaje,
    semaforo,
    tarjeta_alerta,
    tarjeta_ciclo,
    tarjeta_item,
    tarjeta_kpi,
)
from core.protocolo import EVERY_DAY, WEEKDAY_LABELS, weekdays_of
from services import doctor, simulador, vistas
from services.vistas import ItemKind

FORM_PAGE: dict[ItemKind, str] = {"pill": "receta", "meal": "plan_comida", "notice": "aviso"}
SECTION_TITLE: dict[ItemKind, str] = {"pill": "Pills", "meal": "Meals", "notice": "Notifications"}
PICKER_TITLE: dict[ItemKind, str] = {
    "pill": "Prescription writing", "meal": "Meal Assignment", "notice": "Notifications",
}
ITEM_EXTRAS: dict[ItemKind, tuple[str, ...]] = {          # Fields shown under the name
    "pill": ("dose",), "meal": ("meal_type", "target_calories_kcal"), "notice": ("message",),
}
MEAL_TYPE_LABEL = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner", "snack": "Snack"}
SEVERITY_LABEL = {"red": "🔴 Red", "orange": "🟠 Orange", "yellow": "🟡 Yellow"}
TRIAGE_LABEL = {"green": "🟢 Green", "yellow": "🟡 Yellow", "red": "🔴 Red"}
# Patients table: header -> relative width, and the row field shown in each column
PATIENT_COLUMNS = {"Name": 2, "Last name": 3, "Sex": 1, "Age": 1, "Monitoring mode": 2}
PATIENT_KEYS = ("name", "last_name", "sex", "age", "monitoring_mode")

LIVE_METRICS = {
    "heart_rate_bpm": ("Heart rate", "bpm"),
    "spo2_pct": ("SpO₂", "%"),
    "skin_temperature_c": ("Skin temp.", "°C"),
    "respiratory_rate_rpm": ("Resp. rate", "rpm"),
    "estimated_systolic_bp_mmhg": ("Systolic BP", "mmHg"),
}


def header(person: Mapping[str, Any]) -> None:
    """Name, last name, sex and birth date rendered with the Figma header widget."""
    ficha_paciente(person)


def patient_picker(title: str, action: str, target_page: str) -> None:
    """Searchable crew table; the action button opens ``target_page`` for that astronaut."""
    st.title(title)
    search = st.text_input("Search", placeholder="Search patient information", label_visibility="collapsed")
    widths = [*PATIENT_COLUMNS.values(), 2]
    encabezado_tabla([*PATIENT_COLUMNS, "Medical history"], widths)
    for row in vistas.crew(search):
        cols = st.columns(widths, vertical_alignment="center")
        for column, key in zip(cols, PATIENT_KEYS, strict=False):
            column.write(str(row[key]))
        if cols[-1].button(action, key=f"{target_page}_{row['id_log']}", type="primary", width="stretch"):
            st.session_state.pop(EDITING, None)              # From the list it is always a new one
            open_patient(row["id_log"], target_page)


def assignment_page(kind: ItemKind) -> None:
    """Doctor: choose the astronaut to assign to. Astronaut: their own list, read only."""
    user = current_user()
    if is_doctor(user):
        patient_picker(PICKER_TITLE[kind], "Select", FORM_PAGE[kind])
        return
    st.title(SECTION_TITLE[kind])
    items_section(kind, vistas.active_items(kind, user["id"]))


def _render_daily_reports_section(reports: list[dict[str, Any]], id_log: int) -> None:
    """Renders the Figma Daily Health Reports panel using KPI cards, gauge and Plotly trends."""
    if not reports:
        st.info("No daily health reports available yet.")
        return
    options = {r["mission_day"]: f"Day {r['mission_day']} — {r['report_date']}" for r in reports}
    top_left, top_right = st.columns([2, 1], vertical_alignment="center")
    chosen_day = top_left.selectbox(
        "Mission day report",
        options=list(options.keys()),
        format_func=options.get,
        key=f"rep_day_{id_log}",
    )
    rep = next((r for r in reports if r["mission_day"] == chosen_day), reports[0])
    top_right.markdown(
        f"<div style='background:#FFFFFF; padding:8px 16px; border-radius:6px; "
        f"text-align:center; font-size:22px; font-weight:600; color:#233746;'>"
        f"{rep['report_date']}</div>",
        unsafe_allow_html=True,
    )

    g_col, k_col = st.columns([1.2, 2.8], vertical_alignment="center")
    with g_col:
        semaforo(rep.get("triage_status"), f"Day {rep['mission_day']} Triage")
        medidor_puntaje(rep.get("readiness_score"), "Readiness Score")
    with k_col:
        r1 = st.columns(3)
        with r1[0]:
            sleep_h = round((rep.get("sleep_total_min") or 0) / 60.0, 1)
            tarjeta_kpi("Sleep Duration", sleep_h, "h", estado=rep.get("triage_status") or "normal")
        with r1[1]:
            tarjeta_kpi("Resting HR", rep.get("resting_hr_bpm"), "bpm")
        with r1[2]:
            tarjeta_kpi("HRV RMSSD", rep.get("hrv_rmssd_daily_ms"), "ms")

        r2 = st.columns(3)
        with r2[0]:
            tarjeta_kpi("Skin Temp Δ", rep.get("skin_temp_delta_c"), "°C")
        with r2[1]:
            tarjeta_kpi("SpO₂", rep.get("spo2_daily_pct"), "%")
        with r2[2]:
            tarjeta_kpi("Sampling Mode", rep.get("recommended_sampling_mode") or "standard")

    st.markdown("#### Physiological Trends")
    panel_tendencias(reports)


def _render_simulator_box(id_log: int, last_day: int) -> None:
    """Doctor-only controls to launch or revert a critical clinical simulation."""
    st.divider()
    st.markdown("#### Emergency Scenario Simulator")
    active = simulador.active_simulation(id_log)
    if active:
        st.warning(
            f"Active simulated scenario: **{active['scenario']}** "
            f"(Days {active['start_day']}–{active['end_day']})"
        )
        if st.button("Revert simulation", key=f"rev_sim_{id_log}", type="primary"):
            simulador.revert_simulation(active["id"], reverted_by=current_user()["id"])
            st.rerun()
        return

    scenarios = simulador.available_scenarios()
    labels = {s["scenario"]: f"{s['scenario']} — {s['description']}" for s in scenarios}
    c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="bottom")
    chosen = c1.selectbox("Scenario", list(labels.keys()), format_func=labels.get, key=f"scen_{id_log}")
    default_start = max(1, last_day - 3)
    start_day = int(
        c2.number_input(
            "Start day",
            min_value=1,
            max_value=max(1, last_day),
            value=default_start,
            key=f"sday_{id_log}",
        )
    )
    if c3.button("Simulate", key=f"run_sim_{id_log}", type="primary", width="stretch"):
        try:
            simulador.launch_simulation(
                id_log, chosen, start_day, launched_by=current_user()["id"]
            )
        except simulador.SimulationError as exc:
            st.error(str(exc))
        else:
            st.rerun()


def detail_sections(detail: Mapping[str, Any], *, editable: bool) -> None:
    """Collapsible sections of the astronaut detail (the doctor can edit the assignments)."""
    id_log = int(detail["id_log"])
    with st.expander("Daily Health Reports"):
        _render_daily_reports_section(detail["reports"], id_log)

    for kind, items in (("pill", detail["pills"]), ("meal", detail["meals"]), ("notice", detail["notices"])):
        with st.expander(SECTION_TITLE[kind]):
            items_section(kind, items, edit_log=id_log if editable else None)

    with st.expander("Alerts"):
        if detail["alerts"]:
            for alert in detail["alerts"]:
                tarjeta_alerta(alert)
        else:
            st.caption("No clinical alerts recorded.")

    with st.expander("Live info"):
        live = detail["live"]
        if live:
            st.caption(f"Last reading: {live['recorded_at']}")
            for column, (key, (label, unit)) in zip(
                st.columns(len(LIVE_METRICS)), LIVE_METRICS.items(), strict=True
            ):
                with column:
                    tarjeta_kpi(label, live.get(key), unit)
        else:
            st.info("No watch readings yet.")
        if editable:
            last_day = int(live["mission_day"]) if live else 16
            _render_simulator_box(id_log, last_day)

    if detail["cycle"] is not None:
        with st.expander("Menstrual Cycle"):
            tarjeta_ciclo(detail["cycle"])


def _days_text(mask: int) -> str:
    return "Every day" if mask == EVERY_DAY else " ".join(WEEKDAY_LABELS[d] for d in weekdays_of(mask))


def _describe_details(kind: ItemKind, item: Mapping[str, Any]) -> str:
    extras = [str(MEAL_TYPE_LABEL.get(item[f], item[f])) for f in ITEM_EXTRAS[kind] if item[f]]
    schedule = f"{item['scheduled_time']} · {_days_text(item['weekdays'])}"
    return " · ".join([*extras, schedule])


def items_section(kind: ItemKind, items: list[dict[str, Any]], *, edit_log: int | None = None) -> None:
    """Pills, meals or notices of an astronaut. With ``edit_log`` the doctor can add, update or retire."""
    editable = edit_log is not None
    search, new = st.columns([3, 1], vertical_alignment="bottom")
    text = search.text_input("Search", key=f"search_{kind}", placeholder="Search",
                             label_visibility="collapsed").strip().lower()
    if editable and new.button("NEW ASSIGNMENT", key=f"new_{kind}", type="primary", width="stretch"):
        _edit(kind, None, edit_log)
    shown = [item for item in items if text in item["name"].lower()]
    for item in shown:
        deleted, updated = tarjeta_item(
            item["name"],
            _describe_details(kind, item),
            key_prefix=f"{kind}_{item['id']}",
            editable=editable,
        )
        if deleted:
            doctor.retire_item(kind, item["id"], doctor_id=current_user()["id"])
            st.rerun()
        if updated and edit_log is not None:
            _edit(kind, item["id"], edit_log)
    if not shown:
        st.caption("No active assignments.")


def _edit(kind: ItemKind, item_id: int | None, id_log: int) -> None:
    st.session_state[EDITING] = (kind, item_id)
    open_patient(id_log, FORM_PAGE[kind])


def back_to_detail() -> None:
    st.session_state.pop(EDITING, None)
    go("detalle")
