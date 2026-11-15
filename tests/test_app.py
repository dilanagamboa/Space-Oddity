"""UI smoke tests with Streamlit's AppTest: login, menu per role and the doctor's forms."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from components.navegacion import SELECTED_LOG, USER
from scripts.generacion.perfiles import DEMO_PASSWORD, DOCTOR
from services import vistas
from services.auth import authenticate

APP = str(Path(__file__).parents[1] / "app.py")


@pytest.fixture
def db_path(mission_copy, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = mission_copy(16, 5)
    monkeypatch.setenv("SPACE_ODDITY_DB", str(path))
    return path


def _app(email: str | None = None, page: str | None = None, **state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    if email:
        at.session_state[USER] = authenticate(email, DEMO_PASSWORD).user
    for key, value in state.items():
        at.session_state[key] = value
    if page:
        at.switch_page(f"pages/{page}.py")
    return at.run()


def _click(at: AppTest, label: str) -> AppTest:
    return next(b for b in at.button if b.label == label).click().run()


def _menu(at: AppTest) -> set[str]:
    return {b.help for b in at.sidebar.button}


def test_login_with_wrong_and_right_password(db_path: Path) -> None:
    at = _app()
    at.text_input[0].input(DOCTOR[1])
    at.text_input[1].input("mala")
    _click(at, "Login")
    assert at.error[0].value == "Wrong email or password."
    at.text_input[1].input(DEMO_PASSWORD)
    _click(at, "Login")
    assert at.session_state[USER]["email"] == DOCTOR[1] and not at.exception


def test_menu_per_role(db_path: Path) -> None:
    doctor, astronaut = _app(DOCTOR[1]), _app("andrea.solis@spaceoddity.dev")
    assert "Patients" in _menu(doctor) and "Patients" not in _menu(astronaut)
    assert _menu(astronaut) == {"Home", "Pills", "Notifications", "Meals", "My user", "Log out"}
    assert not doctor.exception and not astronaut.exception
    assert len(astronaut.metric) == 4                      # Only their own status


@pytest.mark.parametrize("page", ["pacientes", "medicamentos", "comidas", "notificaciones", "perfil"])
def test_doctor_pages_render(db_path: Path, page: str) -> None:
    assert not _app(DOCTOR[1], page).exception


@pytest.mark.parametrize("page", ["medicamentos", "comidas", "perfil"])
def test_astronaut_sees_but_cannot_assign(db_path: Path, page: str) -> None:
    at = _app("andrea.solis@spaceoddity.dev", page)
    assert not at.exception
    assert not [b for b in at.main.button if b.label in {"NEW ASSIGNMENT", "Update"}]


def test_astronaut_marks_a_notification_as_seen(db_path: Path) -> None:
    at = _app("andrea.solis@spaceoddity.dev", "notificaciones")
    pending = [b for b in at.main.button if b.label == "Mark as seen"]
    _click(at, "Mark as seen")
    assert len([b for b in at.main.button if b.label == "Mark as seen"]) == len(pending) - 1


def test_doctor_assigns_a_notice_on_chosen_days(db_path: Path) -> None:
    at = _app(DOCTOR[1], "aviso", **{SELECTED_LOG: 1})
    at.text_input(key="notice_name").input("Llamada con familia")
    at.button_group(key="notice_weekdays").set_value(["L", "V"])
    _click(at, "Save")
    assert not at.exception
    (notice,) = vistas.astronaut_detail(1, db_path=db_path)["notices"]
    assert notice["name"] == "Llamada con familia" and notice["weekdays"] == 0b10001


def test_detail_and_prescription(db_path: Path) -> None:
    assert not _app(DOCTOR[1], "detalle", **{SELECTED_LOG: 1}).exception
    at = _app(DOCTOR[1], "receta", **{SELECTED_LOG: 1})
    at.text_input(key="pill_name").input("Ondansetrón")
    at.text_input(key="pill_dose").input("4 mg")
    _click(at, "Save")
    assert not at.exception
    assert "Ondansetrón" in {p["name"] for p in vistas.astronaut_detail(1, db_path=db_path)["pills"]}
