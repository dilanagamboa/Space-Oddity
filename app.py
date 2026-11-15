"""Space Oddity: entry point of the Streamlit app.

Run from the repo root::

    streamlit run app.py
"""

import streamlit as st

from components.navegacion import build_navigation, current_user
from components.sidebar import render_sidebar
from config import BASE_DIR

st.set_page_config(page_title="Space Eddus", page_icon="🚀", layout="wide")
st.html(BASE_DIR / "assets" / "estilo.css")          # Figma layout details the theme cannot express

user = current_user()
navigation = build_navigation(user)
if user:
    render_sidebar(user)
navigation.run()
