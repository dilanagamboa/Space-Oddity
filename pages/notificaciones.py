"""Notifications. Doctor: assigns notices to an astronaut. Astronaut: today's notifications."""

import streamlit as st

from components.navegacion import current_user, is_doctor
from components.paciente import assignment_page
from components.widgets import tarjeta_alerta
from core.enums import EstadoRecordatorio
from services.recordatorios import complete_reminder
from services.vistas import log_of_user, my_notifications

user = current_user()
if is_doctor(user):
    assignment_page("notice")
    st.stop()

feed = my_notifications(log_of_user(user["id"]))
st.title("Notifications")
for reminder in feed["reminders"]:
    with st.container(border=True):
        text, seen = st.columns([5, 1], vertical_alignment="center")
        hour = reminder["scheduled_at"][11:16]
        text.markdown(f"**{hour} · {reminder['title']}**  \n{reminder['instructions'] or ''}")
        if reminder["status"] != EstadoRecordatorio.PENDIENTE:
            seen.caption("✓ Seen")
        elif seen.button("Mark as seen", key=f"seen_{reminder['id']}", type="primary", width="stretch"):
            complete_reminder(reminder["id"])
            st.rerun()
if not feed["reminders"]:
    st.info("No notifications for today.")

st.subheader("Health alerts")
for alert in feed["alerts"]:
    tarjeta_alerta(alert)
if not feed["alerts"]:
    st.caption("No open alerts.")
