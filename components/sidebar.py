"""Icon bar on the left (design: home, search, pill, bell, meal and the avatar).

The icons are temporary emojis on buttons. Each one is marked with
"ESTO SE CAMBIA POR IMAGEN": replace the emoji in ``MENU_ICONS`` / ``PROFILE_ICON``
(or the ``st.button`` call in ``render_sidebar``) with the final image.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from components.navegacion import PAGES, ROLE_PAGES, go, log_out
from core.enums import Rol

MENU_ICONS: dict[str, str] = {
    "inicio": "🏠",          # ESTO SE CAMBIA POR IMAGEN (casa)
    "pacientes": "🔍",       # ESTO SE CAMBIA POR IMAGEN (lupa)
    "medicamentos": "💊",    # ESTO SE CAMBIA POR IMAGEN (pastilla)
    "notificaciones": "🔔",  # ESTO SE CAMBIA POR IMAGEN (campana)
    "comidas": "🍕",         # ESTO SE CAMBIA POR IMAGEN (pizza)
}
PROFILE_ICON = "👤"          # ESTO SE CAMBIA POR IMAGEN (avatar)
LOGOUT_ICON = "⏻"            # ESTO SE CAMBIA POR IMAGEN (salir)


def render_sidebar(user: dict[str, Any]) -> None:
    """Draws the role's menu buttons, then the avatar (profile) and log out."""
    allowed = ROLE_PAGES[Rol(user["role"])]
    with st.sidebar:
        for page, icon in MENU_ICONS.items():
            if page in allowed and st.button(icon, key=f"nav_{page}", help=PAGES[page].title,
                                             type="tertiary", width="stretch"):
                go(page)
        with st.container(key="nav_bottom"):             # Pinned to the bottom (assets/estilo.css)
            if st.button(PROFILE_ICON, key="nav_perfil", help=PAGES["perfil"].title, type="tertiary",
                         width="stretch"):
                go("perfil")
            if st.button(LOGOUT_ICON, key="nav_salir", help="Log out", type="tertiary", width="stretch"):
                log_out()
