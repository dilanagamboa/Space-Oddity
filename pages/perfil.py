"""My user (design: My_User). Astronauts also see their own health sections."""

import streamlit as st

from components.navegacion import current_user
from components.paciente import detail_sections, header
from services.vistas import astronaut_detail, log_of_user, user_profile

user = current_user()
profile = user_profile(user["id"])

avatar, info = st.columns([1, 6], vertical_alignment="center")
# ESTO SE CAMBIA POR IMAGEN (avatar)
avatar.markdown("<div style='font-size:5rem;text-align:center'>👤</div>", unsafe_allow_html=True)
with info:
    header(profile)
if (id_log := log_of_user(user["id"])) is not None:
    detail_sections(astronaut_detail(id_log), editable=False)
