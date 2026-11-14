"""Esquema de la base de datos definido con SQLAlchemy Core.

Reemplaza a ``scripts/schema.sql``. Al estar en Python, el mismo esquema se crea
en SQLite (desarrollo y tests) y en PostgreSQL/Supabase (producción).

Convenciones:
    * Todas las fechas y horas se guardan en UTC.
    * ``is_simulated`` distingue los datos que crea el simulador del doctor,
      de modo que se pueden borrar sin tocar los datos base.
    * Los valores permitidos de cada columna categórica salen de ``core.enums``.
"""

from __future__ import annotations

from enum import Enum

import sqlalchemy as sa

from core.enums import (
    CategoriaAlerta,
    CategoriaTarea,
    ContextoGlucosa,
    EscenarioSimulacion,
    EstadoAlerta,
    EstadoRecordatorio,
    EstadoSimulacion,
    EstadoTarea,
    FaseCiclo,
    FaseSueno,
    FranjaSaliva,
    FuenteClimaEspacial,
    FuenteRadiacion,
    IntensidadActividad,
    ModoMonitoreo,
    Rol,
    Severidad,
    Sexo,
    TipoComida,
    TipoRecordatorio,
    Triage,
)

# Nombres estables para índices y restricciones (necesario para migraciones).
metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


# ============================================================================
# Helpers
# ============================================================================
def _en(columna: str, enum_cls: type[Enum], nombre: str | None = None) -> sa.CheckConstraint:
    """Crea un CHECK ``columna IN (...)`` con los valores de una enumeración."""
    valores = ", ".join(
        str(m.value) if isinstance(m.value, int) else f"'{m.value}'" for m in enum_cls
    )
    return sa.CheckConstraint(f"{columna} IN ({valores})", name=nombre or columna)


def _id() -> sa.Column:
    return sa.Column("id", sa.Integer, primary_key=True, autoincrement=True)


def _fk(nombre: str, tabla: str, *, nullable: bool = False, ondelete: str = "CASCADE") -> sa.Column:
    return sa.Column(
        nombre, sa.Integer, sa.ForeignKey(f"{tabla}.id", ondelete=ondelete), nullable=nullable
    )


def _simulado() -> sa.Column:
    return sa.Column("is_simulated", sa.Boolean, nullable=False, server_default=sa.false())


def _momento(nombre: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.DateTime(timezone=True), nullable=nullable)


def _creado() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _modo(nombre: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        nombre,
        sa.String(20),
        nullable=nullable,
        server_default=None if nullable else ModoMonitoreo.ESTANDAR.value,
    )


# ============================================================================
# 1. USUARIOS, MISIONES Y ASIGNACIONES
# ============================================================================
users = sa.Table(
    "users", metadata,
    _id(),
    sa.Column("full_name", sa.String(120), nullable=False),
    sa.Column("email", sa.String(254), nullable=False, unique=True),
    sa.Column("role", sa.SmallInteger, nullable=False),
    sa.Column("birth_date", sa.Date, nullable=False),
    sa.Column("sex", sa.SmallInteger, nullable=False),
    # Autenticación (se usa en la fase de login; nullable hasta entonces)
    sa.Column("password_hash", sa.String(255)),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("failed_attempts", sa.SmallInteger, nullable=False, server_default="0"),
    _momento("locked_until", nullable=True),
    _creado(),
    _en("role", Rol),
    _en("sex", Sexo),
    sa.CheckConstraint("failed_attempts >= 0", name="failed_attempts_no_negativo"),
)

missions = sa.Table(
    "missions", metadata,
    _id(),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("radiation_limit_msv", sa.Float, nullable=False, server_default="600.0"),
    sa.Column("start_date", sa.Date, nullable=False),
    sa.Column("end_date", sa.Date),
    sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="fechas_validas"),
)

mission_logs = sa.Table(
    "mission_logs", metadata,
    _id(),
    _fk("id_user", "users"),
    _fk("id_mission", "missions"),
    sa.Column("start_date", sa.Date, nullable=False),
    sa.Column("end_date", sa.Date),
    sa.Column("start_weight_kg", sa.Float, nullable=False),
    sa.Column("start_height_cm", sa.Float, nullable=False),
    sa.Column("start_muscle_pct", sa.Float, nullable=False),
    sa.Column("start_fat_pct", sa.Float, nullable=False),
    sa.Column("baseline_hb_g_dl", sa.Float, server_default="14.5"),
    _modo("active_monitoring_mode"),
    _en("active_monitoring_mode", ModoMonitoreo),
    sa.UniqueConstraint("id_user", "id_mission"),
)

crew_assignments = sa.Table(
    "crew_assignments", metadata,
    _id(),
    _fk("id_doctor", "users"),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.UniqueConstraint("id_doctor", "id_mission_log"),
)

# ============================================================================
# 2. MEDICAMENTOS, COMIDAS, TAREAS Y RECORDATORIOS
# ============================================================================
pills = sa.Table(
    "pills", metadata,
    _id(),
    _fk("id_user", "users"),
    _fk("assigned_by", "users", nullable=True, ondelete="SET NULL"),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("dose", sa.String(60)),
    sa.Column("scheduled_time", sa.Time, nullable=False),
    sa.Column("frequency_days", sa.SmallInteger, nullable=False, server_default="1"),
    sa.Column("start_date", sa.Date, nullable=False),
    sa.Column("end_date", sa.Date),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.CheckConstraint("frequency_days >= 1", name="frecuencia_positiva"),
    sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="fechas_validas"),
)

meals = sa.Table(
    "meals", metadata,
    _id(),
    _fk("id_user", "users"),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("meal_type", sa.String(10), nullable=False),
    sa.Column("target_calories_kcal", sa.Float),
    sa.Column("target_protein_g", sa.Float),
    sa.Column("scheduled_time", sa.Time, nullable=False),
    sa.Column("frequency_days", sa.SmallInteger, nullable=False, server_default="1"),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    _en("meal_type", TipoComida),
    sa.CheckConstraint("frequency_days >= 1", name="frecuencia_positiva"),
)

tasks = sa.Table(
    "tasks", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    _fk("assigned_by", "users", nullable=True, ondelete="SET NULL"),
    sa.Column("title", sa.String(150), nullable=False),
    sa.Column("description", sa.Text),
    sa.Column("category", sa.String(15), nullable=False),
    _momento("due_at"),
    sa.Column("status", sa.String(10), nullable=False, server_default=EstadoTarea.PENDIENTE.value),
    _momento("completed_at", nullable=True),
    _creado(),
    _simulado(),
    _en("category", CategoriaTarea),
    _en("status", EstadoTarea),
)

reminders_log = sa.Table(
    "reminders_log", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("reminder_type", sa.String(15), nullable=False),
    _fk("id_pill", "pills", nullable=True, ondelete="SET NULL"),
    _fk("id_meal", "meals", nullable=True, ondelete="SET NULL"),
    _fk("id_task", "tasks", nullable=True, ondelete="SET NULL"),
    sa.Column("title", sa.String(150), nullable=False),
    sa.Column("instructions", sa.Text),
    _modo("monitoring_mode"),
    sa.Column("mission_day", sa.Integer, nullable=False),
    _momento("scheduled_at"),
    sa.Column("status", sa.String(10), nullable=False, server_default=EstadoRecordatorio.PENDIENTE.value),
    _momento("completed_at", nullable=True),
    sa.Column("intake_pct", sa.Float, server_default="100.0"),
    _simulado(),
    _en("reminder_type", TipoRecordatorio),
    _en("monitoring_mode", ModoMonitoreo),
    _en("status", EstadoRecordatorio),
    sa.CheckConstraint("intake_pct BETWEEN 0 AND 100", name="intake_pct_rango"),
)

# ============================================================================
# 3. MEDICIONES BIOMÉTRICAS Y CLÍNICAS
# ============================================================================
cardiovascular_autonomic = sa.Table(
    "cardiovascular_autonomic", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("mission_day", sa.Integer, nullable=False),
    _momento("recorded_at"),
    sa.Column("heart_rate_bpm", sa.Float, nullable=False),
    sa.Column("hrv_rmssd_ms", sa.Float, nullable=False),
    sa.Column("hrv_sdnn_ms", sa.Float, nullable=False),
    sa.Column("rr_mean_ms", sa.Float, nullable=False),
    sa.Column("rr_intervals_sample_json", sa.JSON),
    sa.Column("estimated_systolic_bp_mmhg", sa.Float),
    sa.Column("estimated_diastolic_bp_mmhg", sa.Float),
    sa.Column("estimated_map_mmhg", sa.Float),
    sa.Column("spo2_pct", sa.Float, nullable=False),
    sa.Column("perfusion_index_pct", sa.Float),
    sa.Column("respiratory_rate_rpm", sa.Float, nullable=False),
    sa.Column("skin_temperature_c", sa.Float, nullable=False),
    sa.Column("skin_temp_deviation_c", sa.Float),
    sa.Column("sleep_stage", sa.SmallInteger, nullable=False),
    sa.Column("enmo_mg", sa.Float, nullable=False),
    sa.Column("activity_intensity_level", sa.SmallInteger, nullable=False),
    _simulado(),
    _en("sleep_stage", FaseSueno),
    _en("activity_intensity_level", IntensidadActividad),
    sa.UniqueConstraint("id_mission_log", "recorded_at", "is_simulated"),
)

blood_measures = sa.Table(
    "blood_measures", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("mission_day", sa.Integer, nullable=False),
    _momento("recorded_at"),
    sa.Column("blood_glucose_mg_dl", sa.Float),
    sa.Column("measurement_context", sa.String(15), nullable=False,
              server_default=ContextoGlucosa.AYUNAS.value),
    sa.Column("hemoglobin_g_dl", sa.Float),
    _simulado(),
    _en("measurement_context", ContextoGlucosa),
    sa.CheckConstraint(
        "blood_glucose_mg_dl IS NOT NULL OR hemoglobin_g_dl IS NOT NULL", name="al_menos_un_valor"
    ),
)

saliva_measures = sa.Table(
    "saliva_measures", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("mission_day", sa.Integer, nullable=False),
    _momento("recorded_at"),
    sa.Column("collection_slot", sa.String(10), nullable=False),
    sa.Column("sample_volume_ul", sa.Integer),
    sa.Column("salivary_cortisol_nmol_l", sa.Float, nullable=False),
    sa.Column("salivary_dhea_pg_ml", sa.Float),
    sa.Column("salivary_dhea_nmol_l", sa.Float, nullable=False),
    sa.Column("salivary_alpha_amylase_u_ml", sa.Float, nullable=False),
    _simulado(),
    _en("collection_slot", FranjaSaliva),
)

body_composition = sa.Table(
    "body_composition", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("mission_day", sa.Integer, nullable=False),
    _momento("recorded_at"),
    sa.Column("weight_kg", sa.Float, nullable=False),
    sa.Column("visceral_fat_level", sa.Float, nullable=False),
    sa.Column("body_water_pct", sa.Float),
    sa.Column("muscle_pct", sa.Float, nullable=False),
    sa.Column("fat_pct", sa.Float),
    sa.Column("is_fasting", sa.Boolean, nullable=False, server_default=sa.true()),
    _simulado(),
)

menstrual_cycles = sa.Table(
    "menstrual_cycles", metadata,
    _id(),
    _fk("id_user", "users"),
    sa.Column("recorded_date", sa.Date, nullable=False),
    sa.Column("menstruating", sa.Boolean, nullable=False),
    sa.Column("cycle_phase", sa.String(10)),
    _en("cycle_phase", FaseCiclo),
    sa.UniqueConstraint("id_user", "recorded_date"),
)

# ============================================================================
# 4. RADIACIÓN Y CLIMA ESPACIAL
# ============================================================================
radiation_measures = sa.Table(
    "radiation_measures", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("mission_day", sa.Integer, nullable=False),
    _momento("recorded_at"),
    sa.Column("daily_dose_msv", sa.Float, nullable=False),
    sa.Column("cumulative_dose_msv", sa.Float, nullable=False),
    sa.Column("weighted_dose_msv", sa.Float, nullable=False),
    sa.Column("solar_event_flag", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("solar_event_type", sa.Text),
    sa.Column("data_source", sa.String(20), nullable=False,
              server_default=FuenteRadiacion.SINTETICA_BASE.value),
    _simulado(),
    _en("data_source", FuenteRadiacion),
)

space_weather_events = sa.Table(
    "space_weather_events", metadata,
    _id(),
    sa.Column("external_id", sa.String(120), nullable=False, unique=True),
    sa.Column("source", sa.String(15), nullable=False),
    sa.Column("event_type", sa.String(30), nullable=False),   # FLR, CME, SEP, GST, proton_flux...
    _momento("start_time"),
    _momento("peak_time", nullable=True),
    _momento("end_time", nullable=True),
    sa.Column("intensity", sa.Float),                          # Clase de llamarada, pfu, Kp...
    sa.Column("intensity_unit", sa.String(20)),
    sa.Column("raw_json", sa.JSON),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    _en("source", FuenteClimaEspacial),
)

reference_values = sa.Table(
    "reference_values", metadata,
    _id(),
    sa.Column("parameter", sa.String(80), nullable=False),     # p. ej. hemoglobin_g_dl
    sa.Column("context", sa.String(80), nullable=False),       # p. ej. inflight_day_2, preflight
    sa.Column("value", sa.Float, nullable=False),
    sa.Column("std_dev", sa.Float),
    sa.Column("unit", sa.String(20), nullable=False),
    sa.Column("sample_size", sa.Integer),
    sa.Column("source_name", sa.String(150), nullable=False),  # p. ej. NASA OSDR OSD-569
    sa.Column("source_url", sa.Text),
    _momento("retrieved_at"),
    sa.UniqueConstraint("parameter", "context", "source_name"),
)

# ============================================================================
# 5. REPORTES DIARIOS
# ============================================================================
daily_health_reports = sa.Table(
    "daily_health_reports", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("mission_day", sa.Integer, nullable=False),
    sa.Column("report_date", sa.Date, nullable=False),
    # Arquitectura del sueño
    sa.Column("sleep_total_min", sa.Integer, nullable=False),
    sa.Column("sleep_awake_min", sa.Integer, nullable=False),
    sa.Column("sleep_light_min", sa.Integer, nullable=False),
    sa.Column("sleep_deep_min", sa.Integer, nullable=False),
    sa.Column("sleep_rem_min", sa.Integer, nullable=False),
    sa.Column("sleep_efficiency_pct", sa.Float, nullable=False),
    # Cardiovascular, autonómico y acelerometría
    sa.Column("resting_hr_bpm", sa.Float),
    sa.Column("hrv_rmssd_daily_ms", sa.Float),
    sa.Column("hrv_sdnn_daily_ms", sa.Float),
    sa.Column("hrv_baseline_28d_ms", sa.Float),
    sa.Column("hrv_z_score", sa.Float),
    sa.Column("nocturnal_skin_temp_c", sa.Float),
    sa.Column("skin_temp_baseline_28d_c", sa.Float),
    sa.Column("skin_temp_delta_c", sa.Float),
    sa.Column("respiratory_rate_daily_rpm", sa.Float),
    sa.Column("spo2_daily_pct", sa.Float),
    sa.Column("enmo_daily_mean_mg", sa.Float),
    sa.Column("sedentary_min", sa.Integer),
    sa.Column("light_activity_min", sa.Integer),
    sa.Column("mvpa_min", sa.Integer),
    # Antropometría, sarcopenia y GLIM
    sa.Column("bmi", sa.Float),
    sa.Column("delta_mass_pct", sa.Float),
    sa.Column("smi_total_kg_m2", sa.Float),
    sa.Column("bmr_mifflin_kcal", sa.Float),
    sa.Column("glim_malnutrition_risk", sa.SmallInteger, server_default="0"),
    # Eje HPA (nulos en días sin saliva)
    sa.Column("car_absolute_nmol_l", sa.Float),
    sa.Column("car_pct", sa.Float),
    sa.Column("cortisol_diurnal_slope", sa.Float),
    sa.Column("aucg_cortisol", sa.Float),
    sa.Column("aucg_saa", sa.Float),
    sa.Column("sns_hpa_asymmetry_idx", sa.Float),
    sa.Column("cortisol_dhea_molar_ratio", sa.Float),
    # Variabilidad glucémica (nulos en días sin glucosa)
    sa.Column("glucose_cv_pct", sa.Float),
    sa.Column("glucose_tir_pct", sa.Float),
    sa.Column("gmi_pct", sa.Float),
    # Liebermeister, DO2 y radiación
    sa.Column("liebermeister_dissociation_flag", sa.Boolean, server_default=sa.false()),
    sa.Column("do2_ml_min", sa.Float),
    sa.Column("anemia_suspicion_flag", sa.Boolean, server_default=sa.false()),
    sa.Column("cumulative_radiation_msv", sa.Float),
    # Preparación operativa y muestreo adaptativo
    sa.Column("readiness_score", sa.Float, nullable=False),
    sa.Column("triage_status", sa.String(10), nullable=False),
    _modo("recommended_sampling_mode"),
    _simulado(),
    sa.CheckConstraint("glim_malnutrition_risk IN (0, 1, 2)", name="glim_rango"),
    sa.CheckConstraint("readiness_score BETWEEN 0 AND 100", name="readiness_rango"),
    _en("triage_status", Triage),
    _en("recommended_sampling_mode", ModoMonitoreo),
    sa.UniqueConstraint("id_mission_log", "mission_day", "is_simulated"),
)

# ============================================================================
# 6. ALERTAS, SIMULACIONES Y AUDITORÍA
# ============================================================================
alerts = sa.Table(
    "alerts", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    sa.Column("mission_day", sa.Integer, nullable=False),
    _creado(),
    sa.Column("alert_category", sa.String(20), nullable=False),
    sa.Column("severity", sa.String(10), nullable=False),
    sa.Column("title", sa.String(150), nullable=False),
    sa.Column("triggered_rules_json", sa.JSON, nullable=False),
    sa.Column("action_plan_text", sa.Text, nullable=False),
    sa.Column("infographic_asset", sa.Text),
    _modo("sampling_mode_triggered", nullable=True),
    sa.Column("status", sa.String(15), nullable=False, server_default=EstadoAlerta.ACTIVA.value),
    sa.Column("email_sent", sa.Boolean, nullable=False, server_default=sa.false()),
    _simulado(),
    _en("alert_category", CategoriaAlerta),
    _en("severity", Severidad),
    _en("sampling_mode_triggered", ModoMonitoreo),
    _en("status", EstadoAlerta),
)

simulation_runs = sa.Table(
    "simulation_runs", metadata,
    _id(),
    _fk("id_mission_log", "mission_logs"),
    _fk("launched_by", "users", nullable=True, ondelete="SET NULL"),
    sa.Column("scenario", sa.String(25), nullable=False),
    sa.Column("params_json", sa.JSON),
    sa.Column("start_day", sa.Integer, nullable=False),
    sa.Column("end_day", sa.Integer, nullable=False),
    _momento("started_at"),
    _momento("ended_at", nullable=True),
    sa.Column("status", sa.String(10), nullable=False, server_default=EstadoSimulacion.ACTIVA.value),
    _en("scenario", EscenarioSimulacion),
    _en("status", EstadoSimulacion),
    sa.CheckConstraint("end_day >= start_day", name="dias_validos"),
)

audit_log = sa.Table(
    "audit_log", metadata,
    _id(),
    _fk("id_user", "users", nullable=True, ondelete="SET NULL"),
    sa.Column("action", sa.String(40), nullable=False),        # login_ok, assign_pill, ...
    sa.Column("entity", sa.String(40)),
    sa.Column("entity_id", sa.Integer),
    sa.Column("details_json", sa.JSON),
    _creado(),
)

# ============================================================================
# 7. ÍNDICES
# ============================================================================
TABLAS_SERIE_TEMPORAL: tuple[sa.Table, ...] = (
    cardiovascular_autonomic,
    blood_measures,
    saliva_measures,
    body_composition,
    radiation_measures,
    daily_health_reports,
)
"""Tablas con ``id_mission_log`` + ``mission_day``: comparten índices y consultas."""

for _t in TABLAS_SERIE_TEMPORAL:
    sa.Index(None, _t.c.id_mission_log, _t.c.mission_day)
    sa.Index(None, _t.c.is_simulated)

sa.Index(None, alerts.c.id_mission_log, alerts.c.status, alerts.c.is_simulated)
sa.Index(None, reminders_log.c.id_mission_log, reminders_log.c.mission_day,
         reminders_log.c.status, reminders_log.c.is_simulated)
sa.Index(None, tasks.c.id_mission_log, tasks.c.status)
sa.Index(None, crew_assignments.c.id_doctor, crew_assignments.c.active)
sa.Index(None, space_weather_events.c.event_type, space_weather_events.c.start_time)
sa.Index(None, audit_log.c.id_user, audit_log.c.created_at)

TABLAS_CON_SIMULADOS: tuple[sa.Table, ...] = tuple(
    t for t in metadata.sorted_tables if "is_simulated" in t.c
)
"""Tablas que el simulador puede limpiar con ``is_simulated = TRUE``."""
