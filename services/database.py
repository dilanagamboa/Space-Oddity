"""Acceso a datos de Space Oddity.

Patrones:
    * **Repository**: cada tabla (o grupo de tablas) tiene una clase con sus
      consultas. El resto del código nunca escribe SQL.
    * **Unit of Work**: ``sesion()`` abre una transacción y entrega todos los
      repositorios sobre la misma conexión. Si algo falla, se revierte todo.

Uso típico::

    from services.database import sesion

    with sesion() as db:
        id_user = db.usuarios.insertar(full_name="Ana", email="ana@x.com", ...)
        db.auditoria.registrar(id_user, "user_created", "users", id_user)
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import StaticPool

from config import obtener_config
from core.enums import EstadoAlerta, EstadoRecordatorio, EstadoTarea, Rol
from services import schema as s

Fila = dict[str, Any]


# ============================================================================
# Engine y transacciones
# ============================================================================
def _activar_pragmas_sqlite(dbapi_conn: Any, _registro: Any) -> None:
    """Activa llaves foráneas y WAL en cada conexión SQLite."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.close()


def crear_engine(url: str, **kwargs: Any) -> Engine:
    """Crea un engine adaptado al motor de la URL.

    Args:
        url: URL SQLAlchemy. ``sqlite://`` (sin ruta) crea una base en memoria.
        **kwargs: Argumentos extra para ``sqlalchemy.create_engine``.

    Returns:
        Engine listo para usar.
    """
    if not url.startswith("sqlite"):
        return sa.create_engine(url, pool_pre_ping=True, **kwargs)

    en_memoria = url in ("sqlite://", "sqlite:///:memory:")
    if en_memoria:
        # Una sola conexión compartida, si no cada conexión vería una base vacía.
        kwargs.setdefault("poolclass", StaticPool)
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    else:
        ruta = Path(url.removeprefix("sqlite:///"))
        ruta.parent.mkdir(parents=True, exist_ok=True)

    engine = sa.create_engine(url, **kwargs)
    sa.event.listen(engine, "connect", _activar_pragmas_sqlite)
    return engine


@lru_cache(maxsize=1)
def obtener_engine() -> Engine:
    """Engine global de la aplicación, construido desde ``config``."""
    return crear_engine(obtener_config().database_url)


def crear_tablas(engine: Engine | None = None) -> None:
    """Crea las tablas que falten. No modifica las existentes."""
    s.metadata.create_all(engine or obtener_engine())


@contextmanager
def sesion(engine: Engine | None = None) -> Iterator[Repositorios]:
    """Unit of Work: abre una transacción y entrega los repositorios.

    Hace commit al salir del bloque y rollback si ocurre una excepción.

    Args:
        engine: Engine a usar. Por defecto, el de la aplicación.

    Yields:
        Contenedor ``Repositorios`` ligado a la transacción.
    """
    with (engine or obtener_engine()).begin() as conn:
        yield Repositorios(conn)


def ahora_utc() -> datetime:
    """Momento actual en UTC. Usar siempre esta función al guardar fechas."""
    return datetime.now(UTC)


# ============================================================================
# Repositorio base
# ============================================================================
class RepositorioBase:
    """Operaciones CRUD comunes a todas las tablas con columna ``id``.

    Las subclases solo definen ``tabla`` y agregan sus consultas específicas.
    """

    tabla: ClassVar[sa.Table]

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    @property
    def c(self) -> sa.sql.ColumnCollection:
        return self.tabla.c

    def _filas(self, consulta: sa.Executable) -> list[Fila]:
        return [dict(f) for f in self._conn.execute(consulta).mappings()]

    def _fila(self, consulta: sa.Executable) -> Fila | None:
        fila = self._conn.execute(consulta).mappings().first()
        return dict(fila) if fila else None

    def insertar(self, **valores: Any) -> int:
        """Inserta una fila y devuelve su ``id``."""
        resultado = self._conn.execute(sa.insert(self.tabla).values(**valores))
        return int(resultado.inserted_primary_key[0])

    def insertar_varios(self, filas: Sequence[Mapping[str, Any]]) -> int:
        """Inserta muchas filas en un solo lote. Devuelve cuántas se insertaron."""
        if not filas:
            return 0
        self._conn.execute(sa.insert(self.tabla), list(filas))
        return len(filas)

    def obtener(self, id_: int) -> Fila | None:
        return self._fila(sa.select(self.tabla).where(self.c.id == id_))

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
        consulta = sa.select(self.tabla).where(
            *(self.c[col] == val for col, val in filtros.items())
        )
        if orden:
            col = self.c[orden.lstrip("-")]
            consulta = consulta.order_by(col.desc() if orden.startswith("-") else col)
        if limite:
            consulta = consulta.limit(limite)
        return self._filas(consulta)

    def actualizar(self, id_: int, **valores: Any) -> bool:
        """Actualiza una fila. Devuelve ``True`` si existía."""
        resultado = self._conn.execute(
            sa.update(self.tabla).where(self.c.id == id_).values(**valores)
        )
        return resultado.rowcount > 0

    def eliminar(self, id_: int) -> bool:
        """Elimina una fila. Devuelve ``True`` si existía."""
        resultado = self._conn.execute(sa.delete(self.tabla).where(self.c.id == id_))
        return resultado.rowcount > 0


class SerieTemporalMixin(RepositorioBase):
    """Consultas para tablas con ``id_mission_log`` + ``mission_day``."""

    def _filtro_simulados(self, incluir_simulados: bool) -> list[sa.ColumnElement[bool]]:
        return [] if incluir_simulados else [self.c.is_simulated.is_(False)]

    def rango_dias(
        self,
        id_mission_log: int,
        desde: int,
        hasta: int,
        *,
        incluir_simulados: bool = True,
    ) -> list[Fila]:
        """Filas entre dos días de misión (inclusive), en orden cronológico."""
        consulta = (
            sa.select(self.tabla)
            .where(
                self.c.id_mission_log == id_mission_log,
                self.c.mission_day.between(desde, hasta),
                *self._filtro_simulados(incluir_simulados),
            )
            .order_by(self.c.mission_day, self.c.id)
        )
        return self._filas(consulta)

    def ultimo_dia(self, id_mission_log: int) -> int | None:
        """Último ``mission_day`` registrado para un log, o ``None`` si no hay datos."""
        consulta = sa.select(sa.func.max(self.c.mission_day)).where(
            self.c.id_mission_log == id_mission_log
        )
        return self._conn.execute(consulta).scalar()


# ============================================================================
# Repositorios específicos
# ============================================================================
class UsuarioRepo(RepositorioBase):
    tabla = s.users

    def por_email(self, email: str) -> Fila | None:
        return self._fila(sa.select(self.tabla).where(self.c.email == email.strip().lower()))

    def insertar(self, **valores: Any) -> int:
        valores["email"] = str(valores["email"]).strip().lower()
        return super().insertar(**valores)

    def por_rol(self, rol: Rol, *, solo_activos: bool = True) -> list[Fila]:
        filtros = {"role": int(rol)}
        if solo_activos:
            filtros["active"] = True
        return self.listar(orden="full_name", **filtros)


class MisionRepo(RepositorioBase):
    tabla = s.missions


class MisionLogRepo(RepositorioBase):
    tabla = s.mission_logs

    def activos(self, id_mission: int | None = None) -> list[Fila]:
        """Logs sin fecha de fin, opcionalmente de una misión."""
        consulta = sa.select(self.tabla).where(self.c.end_date.is_(None))
        if id_mission is not None:
            consulta = consulta.where(self.c.id_mission == id_mission)
        return self._filas(consulta)


class AsignacionRepo(RepositorioBase):
    tabla = s.crew_assignments

    def pacientes_de(self, id_doctor: int) -> list[Fila]:
        """Logs activos que supervisa un doctor, con el nombre del astronauta."""
        ml, u = s.mission_logs, s.users
        consulta = (
            sa.select(ml, u.c.full_name, u.c.email)
            .join(self.tabla, self.c.id_mission_log == ml.c.id)
            .join(u, u.c.id == ml.c.id_user)
            .where(self.c.id_doctor == id_doctor, self.c.active.is_(True))
            .order_by(u.c.full_name)
        )
        return self._filas(consulta)

    def doctores_de(self, id_mission_log: int) -> list[Fila]:
        u = s.users
        consulta = (
            sa.select(u.c.id, u.c.full_name, u.c.email)
            .join(self.tabla, self.c.id_doctor == u.c.id)
            .where(self.c.id_mission_log == id_mission_log, self.c.active.is_(True))
        )
        return self._filas(consulta)

    def supervisa(self, id_doctor: int, id_mission_log: int) -> bool:
        """``True`` si el doctor tiene asignado ese log (para validar permisos)."""
        return bool(self.listar(id_doctor=id_doctor, id_mission_log=id_mission_log, active=True))


class PastillaRepo(RepositorioBase):
    tabla = s.pills

    def activas_de(self, id_user: int) -> list[Fila]:
        return self.listar(id_user=id_user, active=True, orden="scheduled_time")


class ComidaRepo(RepositorioBase):
    tabla = s.meals

    def activas_de(self, id_user: int) -> list[Fila]:
        return self.listar(id_user=id_user, active=True, orden="scheduled_time")


class TareaRepo(RepositorioBase):
    tabla = s.tasks

    def pendientes(self, id_mission_log: int) -> list[Fila]:
        return self.listar(
            id_mission_log=id_mission_log, status=EstadoTarea.PENDIENTE.value, orden="due_at"
        )


class RecordatorioRepo(RepositorioBase):
    tabla = s.reminders_log

    def pendientes_hasta(self, id_mission_log: int, hasta: datetime) -> list[Fila]:
        consulta = (
            sa.select(self.tabla)
            .where(
                self.c.id_mission_log == id_mission_log,
                self.c.status == EstadoRecordatorio.PENDIENTE.value,
                self.c.scheduled_at <= hasta,
            )
            .order_by(self.c.scheduled_at)
        )
        return self._filas(consulta)


class CardiovascularRepo(SerieTemporalMixin):
    tabla = s.cardiovascular_autonomic


class SangreRepo(SerieTemporalMixin):
    tabla = s.blood_measures


class SalivaRepo(SerieTemporalMixin):
    tabla = s.saliva_measures


class ComposicionCorporalRepo(SerieTemporalMixin):
    tabla = s.body_composition


class RadiacionRepo(SerieTemporalMixin):
    tabla = s.radiation_measures


class CicloMenstrualRepo(RepositorioBase):
    tabla = s.menstrual_cycles


class ReporteRepo(SerieTemporalMixin):
    tabla = s.daily_health_reports

    def del_dia(self, id_mission_log: int, mission_day: int, *, simulado: bool = False) -> Fila | None:
        return self._fila(
            sa.select(self.tabla).where(
                self.c.id_mission_log == id_mission_log,
                self.c.mission_day == mission_day,
                self.c.is_simulated.is_(simulado),
            )
        )


class AlertaRepo(RepositorioBase):
    tabla = s.alerts

    def activas(self, id_mission_log: int | None = None) -> list[Fila]:
        filtros: dict[str, Any] = {"status": EstadoAlerta.ACTIVA.value}
        if id_mission_log is not None:
            filtros["id_mission_log"] = id_mission_log
        return self.listar(orden="-created_at", **filtros)

    def cambiar_estado(self, id_alerta: int, estado: EstadoAlerta) -> bool:
        return self.actualizar(id_alerta, status=estado.value)


class ClimaEspacialRepo(RepositorioBase):
    tabla = s.space_weather_events

    def existe(self, external_id: str) -> bool:
        consulta = sa.select(self.c.id).where(self.c.external_id == external_id)
        return self._conn.execute(consulta).first() is not None

    def insertar_nuevos(self, eventos: Sequence[Mapping[str, Any]]) -> int:
        """Inserta solo los eventos cuyo ``external_id`` no existe (caché idempotente)."""
        nuevos = [e for e in eventos if not self.existe(str(e["external_id"]))]
        return self.insertar_varios(nuevos)

    def entre(self, desde: datetime, hasta: datetime, tipo: str | None = None) -> list[Fila]:
        consulta = sa.select(self.tabla).where(self.c.start_time.between(desde, hasta))
        if tipo:
            consulta = consulta.where(self.c.event_type == tipo)
        return self._filas(consulta.order_by(self.c.start_time))


class ValorReferenciaRepo(RepositorioBase):
    tabla = s.reference_values

    def valor(self, parametro: str, contexto: str) -> Fila | None:
        """Primer valor registrado para un parámetro y contexto."""
        return self._fila(
            sa.select(self.tabla)
            .where(self.c.parameter == parametro, self.c.context == contexto)
            .order_by(self.c.id)
        )


class SimulacionRepo(RepositorioBase):
    tabla = s.simulation_runs


class AuditoriaRepo(RepositorioBase):
    tabla = s.audit_log

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

    def __init__(self, conn: Connection) -> None:
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
        """Borra todos los datos con ``is_simulated = TRUE``.

        Args:
            id_mission_log: Si se indica, limpia solo ese log.

        Returns:
            Filas borradas por tabla.
        """
        borradas: dict[str, int] = {}
        # Orden inverso de dependencias: hijos antes que padres.
        for tabla in reversed(s.TABLAS_CON_SIMULADOS):
            consulta = sa.delete(tabla).where(tabla.c.is_simulated.is_(True))
            if id_mission_log is not None:
                consulta = consulta.where(tabla.c.id_mission_log == id_mission_log)
            borradas[tabla.name] = self.conn.execute(consulta).rowcount
        return borradas
