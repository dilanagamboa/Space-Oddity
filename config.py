"""Configuración central de Space Oddity.

Lee los valores sensibles desde variables de entorno o, si no existen, desde
``.streamlit/secrets.toml``. Es el único módulo que sabe de dónde viene la
configuración, de modo que el resto del código solo llama a ``obtener_config()``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
SQLITE_LOCAL_URL: str = f"sqlite:///{(DATA_DIR / 'astro.db').as_posix()}"


@dataclass(frozen=True)
class Config:
    """Valores de configuración inmutables de la aplicación.

    Attributes:
        database_url: URL de conexión SQLAlchemy (SQLite local o PostgreSQL/Supabase).
        nasa_api_key: Clave de api.nasa.gov. ``DEMO_KEY`` si no se configuró.
    """

    database_url: str
    nasa_api_key: str


def _leer_secreto(clave: str) -> Any | None:
    """Busca una clave primero en el entorno y luego en ``st.secrets``.

    Args:
        clave: Nombre de la variable (por ejemplo ``DATABASE_URL``).

    Returns:
        El valor encontrado o ``None`` si no existe en ninguna fuente.
    """
    valor = os.getenv(clave)
    if valor:
        return valor
    try:
        import streamlit as st

        return st.secrets.get(clave)
    except Exception:  # Sin streamlit o sin secrets.toml (tests, scripts, CI)
        return None


def normalizar_url_db(url: str) -> str:
    """Adapta la URL de Supabase/PostgreSQL al driver psycopg 3.

    Supabase entrega URLs ``postgresql://`` o ``postgres://``, que SQLAlchemy
    asociaría al driver psycopg2. Aquí se fuerza ``postgresql+psycopg://``.

    Args:
        url: URL tal como viene del proveedor.

    Returns:
        URL lista para ``sqlalchemy.create_engine``.
    """
    for prefijo in ("postgres://", "postgresql://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo):]
    return url


@lru_cache(maxsize=1)
def obtener_config() -> Config:
    """Construye la configuración una sola vez por proceso.

    Returns:
        Instancia de ``Config``. Si no hay ``DATABASE_URL`` usa SQLite local.
    """
    url = _leer_secreto("DATABASE_URL") or SQLITE_LOCAL_URL
    return Config(
        database_url=normalizar_url_db(str(url)),
        nasa_api_key=str(_leer_secreto("NASA_API_KEY") or "DEMO_KEY"),
    )
