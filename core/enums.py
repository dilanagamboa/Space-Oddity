"""Enumeraciones del dominio.

Son la única fuente de verdad para los valores permitidos. ``services/schema.py``
genera las restricciones CHECK a partir de ellas, así que agregar un valor nuevo
solo requiere tocar este archivo.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


# --- Usuarios -----------------------------------------------------------------
class Rol(IntEnum):
    ASTRONAUTA = 0
    DOCTOR = 1
    ADMIN = 2


class Sexo(IntEnum):
    FEMENINO = 0
    MASCULINO = 1


# --- Monitoreo y recordatorios -------------------------------------------------
class ModoMonitoreo(StrEnum):
    ESTANDAR = "standard"
    ALERTA_INFECCION = "alert_infection"
    ALERTA_ATROFIA = "alert_atrophy"
    ALERTA_CIRCADIANA = "alert_circadian"


class TipoComida(StrEnum):
    DESAYUNO = "breakfast"
    ALMUERZO = "lunch"
    CENA = "dinner"
    MERIENDA = "snack"


class TipoRecordatorio(StrEnum):
    PASTILLA = "pill"
    COMIDA = "meal"
    AVISO = "notice"
    MUESTRA_GLUCOSA = "glucose_sample"
    MUESTRA_SALIVA = "saliva_sample"
    PESAJE_BIA = "bia_weigh_in"
    TAREA = "task"


class EstadoRecordatorio(StrEnum):
    PENDIENTE = "pending"
    COMPLETADO = "completed"
    OMITIDO = "skipped"


class CategoriaTarea(StrEnum):
    EJERCICIO = "exercise"
    MEDICION = "measurement"
    NUTRICION = "nutrition"
    DESCANSO = "rest"
    PSICOLOGICA = "psychological"
    OTRA = "other"


class EstadoTarea(StrEnum):
    PENDIENTE = "pending"
    COMPLETADA = "completed"
    OMITIDA = "skipped"
    CANCELADA = "cancelled"


# --- Mediciones ----------------------------------------------------------------
class FaseSueno(IntEnum):
    DESPIERTO = 0
    LIGERO = 1
    PROFUNDO = 2
    REM = 3


class IntensidadActividad(IntEnum):
    REPOSO = 0
    LIGERA = 1
    MODERADA = 2
    VIGOROSA = 3


class ContextoGlucosa(StrEnum):
    AYUNAS = "fasting"
    POSPRANDIAL_2H = "post_meal_2h"
    ALEATORIA = "random"
    ALERTA_HORARIA = "alert_hourly"


class FranjaSaliva(StrEnum):
    DESPERTAR_0M = "wake_0m"
    DESPERTAR_30M = "wake_30m"
    TARDE = "afternoon"
    ANTES_DORMIR = "bedtime"


class FaseCiclo(StrEnum):
    MENSTRUAL = "menstrual"
    FOLICULAR = "follicular"
    OVULATORIA = "ovulatory"
    LUTEA = "luteal"


# --- Radiación y clima espacial -----------------------------------------------
class FuenteRadiacion(StrEnum):
    SINTETICA_BASE = "synthetic_baseline"
    NASA_DONKI = "nasa_donki"
    NOAA_SWPC = "noaa_swpc"
    SIMULADA = "simulated"


class FuenteClimaEspacial(StrEnum):
    NASA_DONKI = "nasa_donki"
    NOAA_SWPC = "noaa_swpc"


# --- Reportes y alertas --------------------------------------------------------
class Triage(StrEnum):
    VERDE = "green"
    AMARILLO = "yellow"
    ROJO = "red"


class CategoriaAlerta(StrEnum):
    SEPSIS_INFECCION = "sepsis_infection"
    ATROFIA_GLIM = "atrophy_glim"
    HPA_CIRCADIANO = "hpa_circadian"
    CARDIOVASCULAR = "cardiovascular"
    GLUCEMICA = "glycemic"
    RADIACION = "radiation"
    ANEMIA_OXIGENO = "anemia_oxygen"


class Severidad(StrEnum):
    AMARILLA = "yellow"
    NARANJA = "orange"
    ROJA = "red"


class EstadoAlerta(StrEnum):
    ACTIVA = "active"
    RECONOCIDA = "acknowledged"
    RESUELTA = "resolved"


# --- Simulación ----------------------------------------------------------------
class EscenarioSimulacion(StrEnum):
    INFECCION = "infection"
    ATROFIA = "atrophy"
    DISRUPCION_CIRCADIANA = "circadian_disruption"
    TORMENTA_SOLAR = "solar_storm"
    HIPOGLUCEMIA = "hypoglycemia"
    CARDIOVASCULAR = "cardiovascular"
    ANEMIA = "anemia"


class EstadoSimulacion(StrEnum):
    ACTIVA = "active"
    COMPLETADA = "completed"
    REVERTIDA = "reverted"
