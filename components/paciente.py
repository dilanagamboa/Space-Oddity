"""Patient screens shared by several pages: header, patient table and detail sections.

The doctor's list screens of the design (patients, pills, meals, notifications)
are the same table with a different action, so they share ``patient_picker``.
Containers with a ``key`` get a ``st-key-<key>`` CSS class used by assets/estilo.css.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from components.navegacion import EDITING, current_user, go, is_doctor, open_patient
from core.protocolo import EVERY_DAY, WEEKDAY_LABELS, weekdays_of
from services import doctor, vistas
from services.vistas import ItemKind

FORM_PAGE: dict[ItemKind, str] = {"pill": "receta", "meal": "plan_comida", "notice": "aviso"}
SECTION_TITLE: dict[ItemKind, str] = {"pill": "Pills", "meal": "Meals", "notice": "Notifications"}
PICKER_TITLE: dict[ItemKind, str] = {"pill": "Prescription writing", "meal": "Meal Assignment",
                                     "notice": "Notifications"}
ITEM_EXTRAS: dict[ItemKind, tuple[str, ...]] = {          # Fields shown under the name
    "pill": ("dose",), "meal": ("meal_type", "target_calories_kcal"), "notice": ("message",),
}
MEAL_TYPE_LABEL = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner", "snack": "Snack"}
SEVERITY_LABEL = {"red": "🔴 Red", "orange": "🟠 Orange", "yellow": "🟡 Yellow"}
TRIAGE_LABEL = {"green": "🟢 Green", "yellow": "🟡 Yellow", "red": "🔴 Red"}
# Patients table: header -> relative width, and the row field shown in each column
PATIENT_COLUMNS = {"Name": 2, "Last name": 3, "Sex": 1, "Age": 1, "Monitoring mode": 2}
PATIENT_KEYS = ("name", "last_name", "sex", "age", "monitoring_mode")

REPORT_COLUMNS = {
    "mission_day": "Day", "report_date": "Date", "triage_status": "Triage",
    "readiness_score": "Readiness", "sleep_total_min": "Sleep (min)", "resting_hr_bpm": "Resting HR",
    "hrv_rmssd_daily_ms": "HRV (ms)", "skin_temp_delta_c": "Δ temp (°C)", "spo2_daily_pct": "SpO₂ (%)",
}
LIVE_METRICS = {
    "heart_rate_bpm": ("Heart rate", "bpm"), "spo2_pct": ("SpO₂", "%"),
    "skin_temperature_c": ("Skin temp.", "°C"), "respiratory_rate_rpm": ("Resp. rate", "rpm"),
    "estimated_systolic_bp_mmhg": ("Systolic BP", "mmHg"),
}


def header(person: Mapping[str, Any]) -> None:
    """Name, last name, sex and birth date (read-only boxes of the design)."""
    fields = (("Name", person["name"]), ("Last name", person["last_name"]),
              ("Sex", "Female" if person["sex"] == "F" else "Male"), ("Birth date", person["birth_date"]))
    for column, (label, value) in zip(st.columns([2, 3, 2, 2]), fields, strict=True):
        column.text_input(label, value=str(value), disabled=True)


def patient_picker(title: str, action: str, target_page: str) -> None:
    """Searchable crew table; the action button opens ``target_page`` for that astronaut."""
    st.title(title)
    search = st.text_input("Search", placeholder="Search patient information", label_visibility="collapsed")
    widths = [*PATIENT_COLUMNS.values(), 2]
    with st.container(key="patients_header"):
        for column, label in zip(st.columns(widths), [*PATIENT_COLUMNS, "Medical history"], strict=True):
            column.markdown(f"**{label}**")
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


def detail_sections(detail: Mapping[str, Any], *, editable: bool) -> None:
    """Collapsible sections of the astronaut detail (the doctor can edit the assignments)."""
    with st.expander("Daily Health Reports"):
        rows = [{label: r[col] for col, label in REPORT_COLUMNS.items()} for r in detail["reports"]]
        for r in rows:
            r["Triage"] = TRIAGE_LABEL.get(r["Triage"], r["Triage"])
        st.dataframe(rows, hide_index=True, width="stretch")
    for kind, items in (("pill", detail["pills"]), ("meal", detail["meals"]), ("notice", detail["notices"])):
        with st.expander(SECTION_TITLE[kind]):
            items_section(kind, items, edit_log=detail["id_log"] if editable else None)
    with st.expander("Alerts"):
        st.dataframe([{"Day": a["mission_day"], "Severity": SEVERITY_LABEL[a["severity"]],
                       "Alert": a["title"], "Status": a["status"]} for a in detail["alerts"]],
                     hide_index=True, width="stretch")
    with st.expander("Live info"):
        live = detail["live"]
        if live:
            st.caption(f"Last reading: {live['recorded_at']}")
            for column, (key, (label, unit)) in zip(st.columns(len(LIVE_METRICS)), LIVE_METRICS.items(),
                                                    strict=True):
                column.metric(label, f"{live[key]} {unit}")
        else:
            st.info("No watch readings yet.")
    if detail["cycle"] is not None:
        with st.expander("Menstrual Cycle"):
            st.dataframe([{"Date": c["recorded_date"], "Phase": c["cycle_phase"]} for c in detail["cycle"]],
                         hide_index=True, width="stretch")


def _days_text(mask: int) -> str:
    return "Every day" if mask == EVERY_DAY else " ".join(WEEKDAY_LABELS[d] for d in weekdays_of(mask))


def _describe(kind: ItemKind, item: Mapping[str, Any]) -> str:
    extras = [str(MEAL_TYPE_LABEL.get(item[f], item[f])) for f in ITEM_EXTRAS[kind] if item[f]]
    schedule = f"{item['scheduled_time']} · {_days_text(item['weekdays'])}"
    return "  \n".join([f"**{item['name']}**", *extras, schedule])


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
        with st.container(border=True, key=f"card_{kind}_{item['id']}"):
            st.markdown(_describe(kind, item))
            if not editable:
                continue
            retire, _, update = st.columns([1, 6, 2])
            # ESTO SE CAMBIA POR IMAGEN (basurero)
            if retire.button("🗑️", key=f"del_{kind}_{item['id']}", help="Remove", type="tertiary"):
                doctor.retire_item(kind, item["id"], doctor_id=current_user()["id"])
                st.rerun()
            if update.button("Update", key=f"upd_{kind}_{item['id']}", width="stretch"):
                _edit(kind, item["id"], edit_log)
    if not shown:
        st.caption("No active assignments.")


def _edit(kind: ItemKind, item_id: int | None, id_log: int) -> None:
    st.session_state[EDITING] = (kind, item_id)
    open_patient(id_log, FORM_PAGE[kind])


def back_to_detail() -> None:
    st.session_state.pop(EDITING, None)
    go("detalle")
