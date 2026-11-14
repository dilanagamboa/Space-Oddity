"""Configuración central de Space Oddity.

Rutas del proyecto y claves externas. Las claves se leen de variables de entorno
o, si no existen, de ``.streamlit/secrets.toml``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
DB_PATH: Path = DATA_DIR / "astro.db"
SCHEMA_PATH: Path = BASE_DIR / "scripts" / "schema.sql"


@dataclass(frozen=True)
class Config:
    """Valores de configuración inmutables.

    Attributes:
        nasa_api_key: Clave de api.nasa.gov. ``DEMO_KEY`` si no se configuró.
    """

    nasa_api_key: str


def _leer_secreto(clave: str) -> Any | None:
    """Busca una clave primero en el entorno y luego en ``st.secrets``."""
    valor = os.getenv(clave)
    if valor:
        return valor
    try:
        import streamlit as st

        return st.secrets.get(clave)
    except Exception:  # Sin streamlit o sin secrets.toml (tests, scripts, CI)
        return None


@lru_cache(maxsize=1)
def obtener_config() -> Config:
    """Construye la configuración una sola vez por proceso."""
    return Config(nasa_api_key=str(_leer_secreto("NASA_API_KEY") or "DEMO_KEY"))
