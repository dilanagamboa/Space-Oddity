"""Home (design: Menu Doctor / Dashboard). Doctor: whole crew. Astronaut: only themself."""

import streamlit as st

from components.navegacion import current_user, is_doctor, open_patient
from components.paciente import TRIAGE_LABEL
from components.widgets import medidor_puntaje, semaforo, tarjeta_kpi
from services.vistas import crew_status

user = current_user()
doctor = is_doctor(user)
rows = crew_status() if doctor else [r for r in crew_status() if r["id_user"] == user["id"]]

st.title("Dashboard")
st.caption(f"Welcome, {user['full_name']}")
for row in rows:
    with st.container(border=True):
        top_info, top_gauge = st.columns([2.4, 1.2], vertical_alignment="center")
        with top_info:
            st.subheader(row["full_name"])
            s1, s2 = st.columns(2)
            with s1:
                semaforo(row["triage"], "Triage Status")
            with s2:
                tarjeta_kpi("Monitoring Mode", row["monitoring_mode"], estado=row["triage"] or "normal")
        with top_gauge:
            medidor_puntaje(row["readiness"], "Readiness Score")

        day, triage, score, alerts = st.columns(4)
        day.metric("Mission day", row["mission_day"])
        triage.metric("Triage", TRIAGE_LABEL.get(row["triage"], "—"))
        score.metric("Readiness", row["readiness"] if row["readiness"] is not None else "—")
        alerts.metric("Open alerts", row["open_alerts"])
        if doctor and st.button("View", key=f"home_{row['id_log']}", type="primary"):
            open_patient(row["id_log"], "detalle")
