"""Acceso a datos de Space Oddity sobre SQLite (``sqlite3`` de la librería estándar).

Patrones:
    * **Repository**: cada tabla tiene una clase con sus consultas. El resto del
      código nunca escribe SQL.
    * **Unit of Work**: ``sesion()`` abre una transacción y entrega todos los
      repositorios sobre la misma conexión. Si algo falla, se revierte todo.

Conversiones automáticas al guardar:
    * ``datetime`` → texto ``YYYY-MM-DDTHH:MM:SS`` en UTC.
    * ``date`` → ``YYYY-MM-DD`` y ``time`` → ``HH:MM``.
    * ``dict`` / ``list`` → JSON (columnas ``*_json``), y de vuelta al leer.
    * Enumeraciones → su valor.

Uso típico::

    from services.database import sesion

    with sesion() as db:
        id_user = db.usuarios.insertar(full_name="Ana", email="ana@x.com", ...)
        db.auditoria.registrar(id_user, "user_created", "users", id_user)
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from config import DB_PATH
from core.enums import EstadoAlerta, EstadoRecordatorio, EstadoTarea, Rol

Fila = dict[str, Any]

FORMATO_MOMENTO = "%Y-%m-%dT%H:%M:%S"

TABLAS_CON_SIMULADOS: tuple[str, ...] = (
    "alerts",
    "daily_health_reports",
    "reminders_log",
    "tasks",
    "radiation_measures",
    "body_composition",
    "saliva_measures",
    "blood_measures",
    "cardiovascular_autonomic",
)
"""Tablas con ``is_simulated``, en orden seguro para borrar (hijos antes que padres)."""


# ============================================================================
# Conversión de valores
# ============================================================================
def a_texto_momento(valor: datetime) -> str:
    """Convierte un ``datetime`` a texto ISO en UTC (sin zona horaria)."""
    if valor.tzinfo is not None:
        valor = valor.astimezone(UTC).replace(tzinfo=None)
    return valor.strftime(FORMATO_MOMENTO)


def ahora_utc() -> str:
    """Momento actual en UTC, ya formateado para guardar."""
    return a_texto_momento(datetime.now(UTC))


def _a_sql(valor: Any) -> Any:
    """Convierte un valor de Python al tipo que se guarda en SQLite."""
    if isinstance(valor, Enum):
        return valor.value
    if isinstance(valor, datetime):  # Antes que date: datetime hereda de date
        return a_texto_momento(valor)
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, time):
        return valor.strftime("%H:%M")
    if isinstance(valor, dict | list):
        return json.dumps(valor, ensure_ascii=False)
    return valor


def _desde_sql(fila: sqlite3.Row) -> Fila:
    """Convierte una fila a ``dict`` y decodifica las columnas ``*_json``."""
    datos = dict(fila)
    for clave, valor in datos.items():
        if clave.endswith("_json") and isinstance(valor, str):
            datos[clave] = json.loads(valor)
    return datos


# ============================================================================
# Conexión y transacciones
# ============================================================================
def conectar(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Abre una conexión con llaves foráneas activas y filas tipo diccionario.

    Args:
        db_path: Ruta del archivo SQLite.

    Returns:
        Conexión lista para usar.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def sesion(db_path: Path | str = DB_PATH) -> Iterator[Repositorios]:
    """Unit of Work: abre una transacción y entrega los repositorios.

    Hace commit al salir del bloque, rollback si ocurre una excepción y cierra
    la conexión siempre.

    Args:
        db_path: Ruta del archivo SQLite. Por defecto, ``data/astro.db``.

    Yields:
        Contenedor ``Repositorios`` ligado a la transacción.
    """
    conn = conectar(db_path)
    try:
        yield Repositorios(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================================
# Repositorio base
# ============================================================================
class RepositorioBase:
    """Operaciones CRUD comunes a todas las tablas con columna ``id``.

    Las subclases solo definen ``tabla`` y agregan sus consultas específicas.
    Los nombres de columna se validan contra el esquema real, de modo que nunca
    se interpola en el SQL un nombre que no exista.
    """

    tabla: ClassVar[str]
    _columnas_cache: ClassVar[dict[str, frozenset[str]]] = {}

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- Utilidades internas ---------------------------------------------------
    @property
    def columnas(self) -> frozenset[str]:
        """Columnas reales de la tabla (consultadas una vez por proceso)."""
        if self.tabla not in self._columnas_cache:
            filas = self._conn.execute(f"PRAGMA table_info({self.tabla})").fetchall()
            self._columnas_cache[self.tabla] = frozenset(f["name"] for f in filas)
        return self._columnas_cache[self.tabla]

    def _validar(self, nombres: Sequence[str] | Mapping[str, Any]) -> None:
        desconocidas = set(nombres) - self.columnas
        if desconocidas:
            raise ValueError(f"Columnas inexistentes en '{self.tabla}': {sorted(desconocidas)}")

    def _filas(self, sql: str, params: Sequence[Any] = ()) -> list[Fila]:
        return [_desde_sql(f) for f in self._conn.execute(sql, [_a_sql(p) for p in params])]

    def _fila(self, sql: str, params: Sequence[Any] = ()) -> Fila | None:
        fila = self._conn.execute(sql, [_a_sql(p) for p in params]).fetchone()
        return _desde_sql(fila) if fila else None

    # --- CRUD -----------------------------------------------------------------
    def insertar(self, **valores: Any) -> int:
        """Inserta una fila y devuelve su ``id``."""
        self._validar(valores)
        columnas = ", ".join(valores)
        marcas = ", ".join("?" for _ in valores)
        cursor = self._conn.execute(
            f"INSERT INTO {self.tabla} ({columnas}) VALUES ({marcas})",
            [_a_sql(v) for v in valores.values()],
        )
        return int(cursor.lastrowid)

    def insertar_varios(self, filas: Sequence[Mapping[str, Any]]) -> int:
        """Inserta muchas filas con las mismas columnas en un solo lote."""
        if not filas:
            return 0
        nombres = list(filas[0])
        self._validar(nombres)
        sql = (
            f"INSERT INTO {self.tabla} ({', '.join(nombres)}) "
            f"VALUES ({', '.join('?' for _ in nombres)})"
        )
        self._conn.executemany(sql, ([_a_sql(f[n]) for n in nombres] for f in filas))
        return len(filas)

    def obtener(self, id_: int) -> Fila | None:
        return self._fila(f"SELECT * FROM {self.tabla} WHERE id = ?", [id_])

    def listar(
        self,
        *,
        orden: str | None = None,
        limite: int | None = None,
        **filtros: Any,
    ) -> list[Fila]:
        """Lista filas filtrando por igualdad de columnas.

        Args:
            orden: Columna para ordenar. Prefijo ``-`` para descendente.
            limite: Máximo de filas.
            **filtros: ``columna=valor``.
        """
        self._validar(filtros)
        sql = f"SELECT * FROM {self.tabla}"
        if filtros:
            sql += " WHERE " + " AND ".join(f"{col} = ?" for col in filtros)
        if orden:
            columna = orden.lstrip("-")
            self._validar([columna])
            sql += f" ORDER BY {columna} {'DESC' if orden.startswith('-') else 'ASC'}"
        if limite:
            sql += f" LIMIT {int(limite)}"
        return self._filas(sql, list(filtros.values()))

    def actualizar(self, id_: int, **valores: Any) -> bool:
        """Actualiza una fila. Devuelve ``True`` si existía."""
        self._validar(valores)
        asignaciones = ", ".join(f"{col} = ?" for col in valores)
        cursor = self._conn.execute(
            f"UPDATE {self.tabla} SET {asignaciones} WHERE id = ?",
            [*(_a_sql(v) for v in valores.values()), id_],
        )
        return cursor.rowcount > 0

    def eliminar(self, id_: int) -> bool:
        """Elimina una fila. Devuelve ``True`` si existía."""
        cursor = self._conn.execute(f"DELETE FROM {self.tabla} WHERE id = ?", [id_])
        return cursor.rowcount > 0


class SerieTemporalMixin(RepositorioBase):
    """Consultas para tablas con ``id_mission_log`` + ``mission_day``."""

    def rango_dias(
        self,
        id_mission_log: int,
        desde: int,
        hasta: int,
        *,
        incluir_simulados: bool = True,
    ) -> list[Fila]:
        """Filas entre dos días de misión (inclusive), en orden cronológico."""
        sql = (
            f"SELECT * FROM {self.tabla} "
            "WHERE id_mission_log = ? AND mission_day BETWEEN ? AND ?"
        )
        if not incluir_simulados:
            sql += " AND is_simulated = 0"
        return self._filas(sql + " ORDER BY mission_day, id", [id_mission_log, desde, hasta])

    def ultimo_dia(self, id_mission_log: int) -> int | None:
        """Último ``mission_day`` registrado para un log, o ``None`` si no hay datos."""
        fila = self._conn.execute(
            f"SELECT MAX(mission_day) FROM {self.tabla} WHERE id_mission_log = ?",
            [id_mission_log],
        ).fetchone()
        return fila[0]


# ============================================================================
# Repositorios específicos
# ============================================================================
class UsuarioRepo(RepositorioBase):
    tabla = "users"

    def insertar(self, **valores: Any) -> int:
        valores["email"] = str(valores["email"]).strip().lower()
        return super().insertar(**valores)

    def por_email(self, email: str) -> Fila | None:
        return self._fila("SELECT * FROM users WHERE email = ?", [email.strip().lower()])

    def por_rol(self, rol: Rol, *, solo_activos: bool = True) -> list[Fila]:
        filtros: dict[str, Any] = {"role": rol}
        if solo_activos:
            filtros["active"] = 1
        return self.listar(orden="full_name", **filtros)


class MisionRepo(RepositorioBase):
    tabla = "missions"


class MisionLogRepo(RepositorioBase):
    tabla = "mission_logs"

    def activos(self, id_mission: int | None = None) -> list[Fila]:
        """Logs sin fecha de fin, opcionalmente de una misión."""
        sql = "SELECT * FROM mission_logs WHERE end_date IS NULL"
        if id_mission is None:
            return self._filas(sql)
        return self._filas(sql + " AND id_mission = ?", [id_mission])


class AsignacionRepo(RepositorioBase):
    tabla = "crew_assignments"

    def pacientes_de(self, id_doctor: int) -> list[Fila]:
        """Logs activos que supervisa un doctor, con el nombre del astronauta."""
        return self._filas(
            """
            SELECT ml.*, u.full_name, u.email
            FROM crew_assignments ca
            JOIN mission_logs ml ON ml.id = ca.id_mission_log
            JOIN users u ON u.id = ml.id_user
            WHERE ca.id_doctor = ? AND ca.active = 1
            ORDER BY u.full_name
            """,
            [id_doctor],
        )

    def doctores_de(self, id_mission_log: int) -> list[Fila]:
        return self._filas(
            """
            SELECT u.id, u.full_name, u.email
            FROM crew_assignments ca
            JOIN users u ON u.id = ca.id_doctor
            WHERE ca.id_mission_log = ? AND ca.active = 1
            """,
            [id_mission_log],
        )

    def supervisa(self, id_doctor: int, id_mission_log: int) -> bool:
        """``True`` si el doctor tiene asignado ese log (para validar permisos)."""
        return bool(self.listar(id_doctor=id_doctor, id_mission_log=id_mission_log, active=1))


class PastillaRepo(RepositorioBase):
    tabla = "pills"

    def activas_de(self, id_user: int) -> list[Fila]:
        return self.listar(id_user=id_user, active=1, orden="scheduled_time")


class ComidaRepo(RepositorioBase):
    tabla = "meals"

    def activas_de(self, id_user: int) -> list[Fila]:
        return self.listar(id_user=id_user, active=1, orden="scheduled_time")


class TareaRepo(RepositorioBase):
    tabla = "tasks"

    def pendientes(self, id_mission_log: int) -> list[Fila]:
        return self.listar(
            id_mission_log=id_mission_log, status=EstadoTarea.PENDIENTE, orden="due_at"
        )


class RecordatorioRepo(RepositorioBase):
    tabla = "reminders_log"

    def pendientes_hasta(self, id_mission_log: int, hasta: datetime) -> list[Fila]:
        return self._filas(
            """
            SELECT * FROM reminders_log
            WHERE id_mission_log = ? AND status = ? AND scheduled_at <= ?
            ORDER BY scheduled_at
            """,
            [id_mission_log, EstadoRecordatorio.PENDIENTE, hasta],
        )


class CardiovascularRepo(SerieTemporalMixin):
    tabla = "cardiovascular_autonomic"


class SangreRepo(SerieTemporalMixin):
    tabla = "blood_measures"


class SalivaRepo(SerieTemporalMixin):
    tabla = "saliva_measures"


class ComposicionCorporalRepo(SerieTemporalMixin):
    tabla = "body_composition"


class RadiacionRepo(SerieTemporalMixin):
    tabla = "radiation_measures"


class CicloMenstrualRepo(RepositorioBase):
    tabla = "menstrual_cycles"


class ReporteRepo(SerieTemporalMixin):
    tabla = "daily_health_reports"

    def del_dia(self, id_mission_log: int, mission_day: int, *, simulado: bool = False) -> Fila | None:
        return self._fila(
            """
            SELECT * FROM daily_health_reports
            WHERE id_mission_log = ? AND mission_day = ? AND is_simulated = ?
            """,
            [id_mission_log, mission_day, int(simulado)],
        )


class AlertaRepo(RepositorioBase):
    tabla = "alerts"

    def activas(self, id_mission_log: int | None = None) -> list[Fila]:
        filtros: dict[str, Any] = {"status": EstadoAlerta.ACTIVA}
        if id_mission_log is not None:
            filtros["id_mission_log"] = id_mission_log
        return self.listar(orden="-created_at", **filtros)

    def cambiar_estado(self, id_alerta: int, estado: EstadoAlerta) -> bool:
        return self.actualizar(id_alerta, status=estado)


class ClimaEspacialRepo(RepositorioBase):
    tabla = "space_weather_events"

    def existe(self, external_id: str) -> bool:
        return self._fila(
            "SELECT id FROM space_weather_events WHERE external_id = ?", [external_id]
        ) is not None

    def insertar_nuevos(self, eventos: Sequence[Mapping[str, Any]]) -> int:
        """Inserta solo los eventos cuyo ``external_id`` no existe (caché idempotente)."""
        nuevos = [e for e in eventos if not self.existe(str(e["external_id"]))]
        return self.insertar_varios(nuevos)

    def entre(self, desde: datetime, hasta: datetime, tipo: str | None = None) -> list[Fila]:
        sql = "SELECT * FROM space_weather_events WHERE start_time BETWEEN ? AND ?"
        params: list[Any] = [desde, hasta]
        if tipo:
            sql += " AND event_type = ?"
            params.append(tipo)
        return self._filas(sql + " ORDER BY start_time", params)


class ValorReferenciaRepo(RepositorioBase):
    tabla = "reference_values"

    def valor(self, parametro: str, contexto: str) -> Fila | None:
        """Primer valor registrado para un parámetro y contexto."""
        return self._fila(
            "SELECT * FROM reference_values WHERE parameter = ? AND context = ? ORDER BY id",
            [parametro, contexto],
        )


class SimulacionRepo(RepositorioBase):
    tabla = "simulation_runs"


class AuditoriaRepo(RepositorioBase):
    tabla = "audit_log"

    def registrar(
        self,
        id_user: int | None,
        accion: str,
        entidad: str | None = None,
        id_entidad: int | None = None,
        detalles: Mapping[str, Any] | None = None,
    ) -> int:
        return self.insertar(
            id_user=id_user,
            action=accion,
            entity=entidad,
            entity_id=id_entidad,
            details_json=dict(detalles) if detalles else None,
        )


# ============================================================================
# Contenedor de la Unit of Work
# ============================================================================
class Repositorios:
    """Todos los repositorios compartiendo una misma conexión/transacción."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.usuarios = UsuarioRepo(conn)
        self.misiones = MisionRepo(conn)
        self.mission_logs = MisionLogRepo(conn)
        self.asignaciones = AsignacionRepo(conn)
        self.pastillas = PastillaRepo(conn)
        self.comidas = ComidaRepo(conn)
        self.tareas = TareaRepo(conn)
        self.recordatorios = RecordatorioRepo(conn)
        self.cardiovascular = CardiovascularRepo(conn)
        self.sangre = SangreRepo(conn)
        self.saliva = SalivaRepo(conn)
        self.composicion = ComposicionCorporalRepo(conn)
        self.ciclos = CicloMenstrualRepo(conn)
        self.radiacion = RadiacionRepo(conn)
        self.clima_espacial = ClimaEspacialRepo(conn)
        self.referencias = ValorReferenciaRepo(conn)
        self.reportes = ReporteRepo(conn)
        self.alertas = AlertaRepo(conn)
        self.simulaciones = SimulacionRepo(conn)
        self.auditoria = AuditoriaRepo(conn)

    def limpiar_simulados(self, id_mission_log: int | None = None) -> dict[str, int]:
        """Borra todos los datos con ``is_simulated = 1``.

        Args:
            id_mission_log: Si se indica, limpia solo ese log.

        Returns:
            Filas borradas por tabla.
        """
        borradas: dict[str, int] = {}
        for tabla in TABLAS_CON_SIMULADOS:
            sql = f"DELETE FROM {tabla} WHERE is_simulated = 1"
            params: list[Any] = []
            if id_mission_log is not None:
                sql += " AND id_mission_log = ?"
                params.append(id_mission_log)
            borradas[tabla] = self.conn.execute(sql, params).rowcount
        return borradas
