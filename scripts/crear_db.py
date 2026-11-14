"""Crea las tablas de Space Oddity en la base configurada.

Usa ``DATABASE_URL`` (Supabase) si existe, o ``data/astro.db`` en local.

Ejecutar desde la raíz del repo::

    python -m scripts.crear_db            # crea lo que falte
    python -m scripts.crear_db --reset    # BORRA todo y lo recrea (solo desarrollo)
"""

from __future__ import annotations

import argparse

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from services.database import obtener_engine
from services.schema import metadata


def crear_base_datos(engine: Engine | None = None, *, reset: bool = False) -> None:
    """Crea las tablas e índices definidos en ``services/schema.py``.

    Args:
        engine: Engine destino. Por defecto, el de la configuración.
        reset: Si es ``True``, elimina todas las tablas antes de crearlas.
    """
    engine = engine or obtener_engine()
    try:
        if reset:
            metadata.drop_all(engine)
        metadata.create_all(engine)
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Error al crear la base de datos: {exc}") from exc
    print(f"[OK] {len(metadata.tables)} tablas listas en: {engine.url.render_as_string()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="Borra y recrea todas las tablas.")
    args = parser.parse_args()

    if args.reset and input("Esto borra TODOS los datos. Escriba 'si' para continuar: ") != "si":
        print("Cancelado.")
        return
    crear_base_datos(reset=args.reset)


if __name__ == "__main__":
    main()
