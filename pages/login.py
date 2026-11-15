"""Login (design: Login Doctor). Picture on the left, credentials card on the right."""

import streamlit as st

from components.navegacion import USER
from config import BASE_DIR
from services.auth import authenticate

LOGIN_IMAGE = BASE_DIR / "assets" / "ImagenInicio.jpeg"

image, card = st.columns([1, 1.15], gap="medium", vertical_alignment="center")
with image.container(key="login_image"):
    st.image(str(LOGIN_IMAGE), width="stretch")

with card.container(key="login_card"):
    st.markdown("<h1 style='text-align:center'>Welcome to Space Eddus!</h1>", unsafe_allow_html=True)
    with st.form("login", border=False):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        forgot = st.form_submit_button("Did you forget your password?", key="forgot", type="tertiary")
        _, button, _ = st.columns([1, 1.4, 1])
        submitted = button.form_submit_button("Login", type="primary", width="stretch")
    if forgot:
        st.info("Ask the mission administrator to reset it.")
    if submitted:
        result = authenticate(email, password)
        if result.ok:
            st.session_state[USER] = result.user
            st.rerun()
        st.error(result.error)
