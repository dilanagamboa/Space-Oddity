"""Pill, meal and notice forms (design: Write_prescription and Write_Meal).

The forms are built from ``FORMS``: each field says which widget draws it, so the
screens share one implementation. Creating and editing use the same form.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, time
from typing import Any

import streamlit as st

from components.navegacion import EDITING, current_user, selected_log
from components.paciente import MEAL_TYPE_LABEL, back_to_detail, header
from core.enums import TipoComida
from core.protocolo import EVERY_DAY, WEEKDAY_LABELS, weekdays_mask, weekdays_of
from services import doctor, vistas
from services.vistas import ItemKind

Widget = Callable[[str, Any, str], Any]


def _time_value(value: Any) -> time:
    if isinstance(value, time):
        return value
    hour, minute = str(value or "08:00").split(":")[:2]
    return time(int(hour), int(minute))


def _weekdays(label: str, value: int | None, key: str) -> int:
    """L-D toggle buttons (design), returned as the stored bit mask."""
    default = [WEEKDAY_LABELS[d] for d in weekdays_of(EVERY_DAY if value is None else value)]
    chosen = st.pills(label, WEEKDAY_LABELS, selection_mode="multi", default=default, key=key)
    return weekdays_mask(WEEKDAY_LABELS.index(day) for day in chosen)


def _date_value(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


WIDGETS: dict[str, Widget] = {
    "text": lambda label, value, key: st.text_input(label, value=value or "", key=key),
    "long_text": lambda label, value, key: st.text_area(label, value=value or "", key=key),
    "time": lambda label, value, key: st.time_input(label, value=_time_value(value), key=key),
    "weekdays": _weekdays,
    "number": lambda label, value, key: st.number_input(label, min_value=0.0, value=float(value or 0),
                                                        key=key),
    "date": lambda label, value, key: st.date_input(label, value=_date_value(value) or date.today(), key=key),
    "optional_date": lambda label, value, key: st.date_input(label, value=_date_value(value), key=key),
    "meal_type": lambda label, value, key: st.selectbox(
        label, list(TipoComida), index=list(TipoComida).index(value or TipoComida.DESAYUNO),
        format_func=MEAL_TYPE_LABEL.get, key=key),
}

Field = tuple[str, str, str]                       # (field, label, widget)

# kind -> (title, rows); the fields of a row share one line, as in the design
FORMS: dict[ItemKind, tuple[str, list[list[Field]]]] = {
    "pill": ("Write prescription", [
        [("name", "Medication name", "text")],
        [("dose", "Dose", "text")],
        [("scheduled_time", "Time", "time"), ("weekdays", "Frequency", "weekdays")],
        [("start_date", "Start date", "date"), ("end_date", "End date (optional)", "optional_date")],
        [("description", "Description", "long_text")],
    ]),
    "meal": ("Write meal", [
        [("name", "Name", "text"), ("meal_type", "Meal type", "meal_type")],
        [("target_calories_kcal", "Target calories (kcal)", "number"),
         ("target_protein_g", "Target protein (g)", "number")],
        [("scheduled_time", "Time", "time"), ("weekdays", "Frequency", "weekdays")],
    ]),
    "notice": ("Write notification", [
        [("name", "Title", "text")],
        [("message", "Message", "long_text")],
        [("scheduled_time", "Time", "time"), ("weekdays", "Frequency", "weekdays")],
    ]),
}


def item_form(kind: ItemKind, list_page: str) -> None:
    """Create or edit a pill, meal or notice for the selected astronaut, then go back to the detail."""
    id_log = selected_log(list_page)
    detail = vistas.astronaut_detail(id_log)
    editing_kind, item_id = st.session_state.get(EDITING, (kind, None))
    current = (vistas.item(kind, item_id) if editing_kind == kind and item_id else None) or {}
    title, rows = FORMS[kind]

    st.title(title)
    header(detail)
    with st.form(f"form_{kind}", border=False):
        values = {}
        for row in rows:
            for column, (field, label, widget) in zip(st.columns(len(row)), row, strict=True):
                with column:
                    values[field] = WIDGETS[widget](label, current.get(field), f"{kind}_{field}")
        if kind in doctor.SIGNED_KINDS:
            st.text_input("Assigned by", value=current_user()["full_name"], disabled=True)
        _, save, discard = st.columns([4, 1, 1])
        saved = save.form_submit_button("Save", type="primary", width="stretch")
        discarded = discard.form_submit_button("Discard", key="discard", width="stretch")

    if discarded:
        back_to_detail()
    if saved:
        try:
            doctor.save_item(kind, values, id_user=detail["id_user"], doctor_id=current_user()["id"],
                             item_id=item_id if editing_kind == kind else None)
        except ValueError as exc:
            st.error(str(exc))
        else:
            back_to_detail()
