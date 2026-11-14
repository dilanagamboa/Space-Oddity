"""Inicializa el esquema SQLite de astro-health en data/astro.db."""

import sqlite3
from pathlib import Path

BASE_DIR: Path = Path(__file__).resolve().parent.parent
SCHEMA_PATH: Path = BASE_DIR / "scripts" / "schema.sql"
DB_PATH: Path = BASE_DIR / "data" / "astro.db"


def crear_base_datos(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> None:
    """Crea el archivo SQLite y ejecuta el DDL definido en schema.sql.

    Args:
        db_path: Ruta destino del archivo astro.db.
        schema_path: Ruta al archivo schema.sql con las tablas e índices.
    """
    if not schema_path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de esquema: {schema_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql_script: str = schema_path.read_text(encoding="utf-8")

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(sql_script)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.commit()
        print(f"[OK] Base de datos inicializada correctamente en: {db_path}")
    except sqlite3.Error as exc:
        raise RuntimeError(f"Error al crear la base de datos SQLite: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    crear_base_datos()
