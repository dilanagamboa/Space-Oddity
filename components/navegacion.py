"""Page registry, menus per role and the session state shared by every page.

Only the pages of the logged role are registered in ``st.navigation``, so a user
cannot open another role's page even by typing its URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import streamlit as st

from core.enums import Rol


@dataclass(frozen=True)
class PageInfo:
    file: str
    title: str


PAGES: dict[str, PageInfo] = {
    "login": PageInfo("pages/login.py", "Login"),
    "inicio": PageInfo("pages/inicio.py", "Home"),
    "pacientes": PageInfo("pages/pacientes.py", "Patients"),
    "detalle": PageInfo("pages/detalle.py", "Astronaut detail"),
    "medicamentos": PageInfo("pages/medicamentos.py", "Pills"),
    "receta": PageInfo("pages/receta.py", "Prescription"),
    "comidas": PageInfo("pages/comidas.py", "Meals"),
    "plan_comida": PageInfo("pages/plan_comida.py", "Meal plan"),
    "notificaciones": PageInfo("pages/notificaciones.py", "Notifications"),
    "aviso": PageInfo("pages/aviso.py", "Notification"),
    "perfil": PageInfo("pages/perfil.py", "My user"),
}

_DOCTOR_PAGES = tuple(key for key in PAGES if key != "login")
ROLE_PAGES: dict[Rol, tuple[str, ...]] = {
    Rol.DOCTOR: _DOCTOR_PAGES,
    Rol.ADMIN: _DOCTOR_PAGES,                       # No admin screens designed yet
    Rol.ASTRONAUTA: ("inicio", "medicamentos", "notificaciones", "comidas", "perfil"),   # Read only
}

# Session state keys
USER = "user"
SELECTED_LOG = "selected_log"
EDITING = "editing"          # (kind, item_id | None) of the pill, meal or notice being edited


def current_user() -> dict[str, Any] | None:
    return st.session_state.get(USER)


def is_doctor(user: dict[str, Any]) -> bool:
    return Rol(user["role"]) in (Rol.DOCTOR, Rol.ADMIN)


def build_navigation(user: dict[str, Any] | None) -> Any:
    """``st.navigation`` with only the pages of the user's role (login if logged out)."""
    keys = ROLE_PAGES[Rol(user["role"])] if user else ("login",)
    pages = [st.Page(PAGES[k].file, title=PAGES[k].title, url_path=k, default=i == 0)
             for i, k in enumerate(keys)]
    return st.navigation(pages, position="hidden")


def go(page: str) -> None:
    st.switch_page(PAGES[page].file)


def open_patient(id_log: int, page: str) -> None:
    """Remembers the selected astronaut and opens a page about them."""
    st.session_state[SELECTED_LOG] = id_log
    go(page)


def selected_log(fallback_page: str) -> int:
    """Selected astronaut, or back to ``fallback_page`` if none was chosen."""
    id_log = st.session_state.get(SELECTED_LOG)
    if id_log is None:
        go(fallback_page)
    return id_log


def log_out() -> None:
    st.session_state.clear()
    st.rerun()
