"""Astronaut detail (design: Detalle Astronauta). Doctor only."""

import streamlit as st

from components.navegacion import go, selected_log
from components.paciente import detail_sections, header
from services.vistas import astronaut_detail

if st.button("← Patients", type="tertiary"):
    go("pacientes")
detail = astronaut_detail(selected_log("pacientes"))
header(detail)
detail_sections(detail, editable=True)
