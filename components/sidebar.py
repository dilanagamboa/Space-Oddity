"""Icon bar on the left (design: home, search, pill, bell, meal and the avatar).

Each menu button carries the key ``icon_<name>``: assets/estilo.css hides its text and
draws ``static/iconos/<name>.png`` (served by Streamlit's static files) as its picture.
The text stays as the page title, so the button keeps its tooltip and accessible name.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from components.navegacion import PAGES, ROLE_PAGES, go, log_out
from config import BASE_DIR
from core.enums import Rol

ICON_DIR = BASE_DIR / "static" / "iconos"
ICON_CSS = ".st-key-icon_{0} button {{ background-image: url('app/static/iconos/{0}.png'); }}"

MENU_ICONS: dict[str, str] = {           # page -> icon file name (static/iconos/<name>.png)
    "inicio": "casa",
    "pacientes": "lupa",
    "medicamentos": "pastilla",
    "notificaciones": "campana",
    "comidas": "pizza",
}
PROFILE_ICON = "persona"
LOGOUT_ICON = "⏻"                        # ESTO SE CAMBIA POR IMAGEN (salir)


def _icon_button(page: str, icon: str) -> bool:
    title = PAGES[page].title
    return st.button(title, key=f"icon_{icon}", help=title, type="tertiary", width="stretch")


def render_sidebar(user: dict[str, Any]) -> None:
    """Draws the role's menu icons, then the avatar (profile) and log out."""
    allowed = ROLE_PAGES[Rol(user["role"])]
    icons = (*MENU_ICONS.values(), PROFILE_ICON)
    st.html("<style>" + "".join(ICON_CSS.format(icon) for icon in icons) + "</style>")
    with st.sidebar:
        for page, icon in MENU_ICONS.items():
            if page in allowed and _icon_button(page, icon):
                go(page)
        with st.container(key="nav_bottom"):             # Pinned to the bottom (assets/estilo.css)
            if _icon_button("perfil", PROFILE_ICON):
                go("perfil")
            if st.button(LOGOUT_ICON, key="nav_salir", help="Log out", type="tertiary", width="stretch"):
                log_out()
