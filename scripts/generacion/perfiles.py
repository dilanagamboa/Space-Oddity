"""Mission configuration, fictional crew and literature parameters.

Every literature value used by the generator is listed in ``REFERENCE_VALUES`` and
stored in the ``reference_values`` table with its source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from core.enums import Sexo
from core.escenarios import FEVER_HR_BPM_PER_C

# =====================================================================
# 1. LITERATURE PARAMETERS (all stored in reference_values)
# =====================================================================

# Daily dose by environment (mSv/day). ISS: midpoint of 0.4-0.7 (team document).
DOSE_RATE_MSV_DAY: dict[str, float] = {"iss": 0.55, "lunar": 1.369, "deep_space": 1.8}


ISS_MEAN_SLEEP_H: float = 6.09        # Barger 2014
CAR_MEAN_INCREASE: float = 0.50       # Wüst 2000: about +50 % at 30 min
LUTEAL_SKIN_TEMP_C: float = 0.3       # Physiological luteal rise (+0.3 to +0.5 °C)

# Day-to-day variability of nocturnal values (team assumption, in line with the ~10 %
# day-to-day variation of ln RMSSD reported for healthy adults). Without it the
# personal baselines would have an unrealistically small standard deviation.
DAILY_HRV_LOG_SD: float = 0.10
DAILY_HR_SD_BPM: float = 1.8
DAILY_SKIN_TEMP_SD_C: float = 0.08
DAILY_RR_SD_RPM: float = 0.4

REFERENCE_VALUES: tuple[dict[str, Any], ...] = (
    {"parameter": "sleep_hours", "context": "iss_mean", "value": 6.09, "unit": "h",
     "source_name": "Barger LK et al. Lancet Neurol 2014",
     "source_url": "https://doi.org/10.1016/S1474-4422(14)70122-X"},
    {"parameter": "sleep_hours", "context": "recommended_min", "value": 7.0, "unit": "h",
     "source_name": "Watson NF et al. Sleep 2015 (AASM/SRS)",
     "source_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC4434546/"},
    {"parameter": "hrv_rmssd_ms", "context": "healthy_adult_mean", "value": 42.0, "unit": "ms",
     "source_name": "Nunan D et al. PACE 2010",
     "source_url": "https://doi.org/10.1111/j.1540-8159.2010.02841.x"},
    {"parameter": "hrv_sdnn_ms", "context": "healthy_adult_mean", "value": 50.0, "unit": "ms",
     "source_name": "Nunan D et al. PACE 2010",
     "source_url": "https://doi.org/10.1111/j.1540-8159.2010.02841.x"},
    {"parameter": "car_pct", "context": "mean_increase_30min", "value": 50.0, "unit": "%",
     "sample_size": 509, "source_name": "Wüst S et al. Noise Health 2000",
     "source_url": "https://pubmed.ncbi.nlm.nih.gov/12689474/"},
    {"parameter": "salivary_alpha_amylase_u_ml", "context": "adult_mean", "value": 92.4,
     "unit": "U/mL", "source_name": "Salimetrics example ranges",
     "source_url": "https://salimetrics.com/analyte/salivary-alpha-amylase"},
    {"parameter": "fever_hr_increase", "context": "per_degree_c", "value": FEVER_HR_BPM_PER_C,
     "unit": "bpm/°C",
     "source_name": "Karjalainen J, Viitasalo M. Arch Intern Med 1986",
     "source_url": "https://pubmed.ncbi.nlm.nih.gov/2424378/"},
    {"parameter": "radiation_dose_msv_day", "context": "iss", "value": 0.731, "unit": "mSv/day",
     "source_name": "Zhang S et al. Sci Adv 2020",
     "source_url": "https://doi.org/10.1126/sciadv.aaz1334"},
    {"parameter": "radiation_dose_msv_day", "context": "lunar_surface", "value": 1.369,
     "unit": "mSv/day", "source_name": "Zhang S et al. Sci Adv 2020",
     "source_url": "https://doi.org/10.1126/sciadv.aaz1334"},
    {"parameter": "radiation_dose_msv_day", "context": "deep_space_transit", "value": 1.8,
     "unit": "mSv/day", "source_name": "Hassler DM et al. Science 2014",
     "source_url": "https://doi.org/10.1126/science.1244797"},
    {"parameter": "radiation_limit_msv", "context": "career", "value": 600.0, "unit": "mSv",
     "source_name": "NASA OCHMO, NASA-STD-3001",
     "source_url": "https://www.nasa.gov/wp-content/uploads/2023/03/radiation-protection-technical-brief-ochmo.pdf"},
)


# =====================================================================
# 2. CONFIGURATION
# =====================================================================

@dataclass(frozen=True)
class AstronautProfile:
    """Fictional astronaut and their personal physiological baseline."""

    full_name: str
    email: str
    sex: Sexo
    birth_date: date
    height_cm: float
    weight_kg: float
    muscle_pct: float
    fat_pct: float
    visceral_fat_level: float
    resting_hr_bpm: float
    rmssd_ms: float
    baseline_hb_g_dl: float
    scenario: str = "none"
    cycle_offset_days: int = 0


@dataclass(frozen=True)
class MissionConfig:
    """Parameters of the generated mission."""

    name: str = "Space Oddity LEO-1"
    environment: str = "iss"
    start_date: date = date(2024, 5, 1)  # Includes the real May 2024 solar storms
    days: int = 50
    seed: int = 42


DEFAULT_MISSION = MissionConfig()

CREW: tuple[AstronautProfile, ...] = (
    AstronautProfile("Andrea Solís", "andrea.solis@spaceoddity.dev", Sexo.FEMENINO,
                     date(1988, 4, 12), 165.0, 61.0, 34.0, 24.0, 6, 62, 45, 13.8,
                     scenario="glycemic", cycle_offset_days=3),
    AstronautProfile("Mateo Rojas", "mateo.rojas@spaceoddity.dev", Sexo.MASCULINO,
                     date(1985, 9, 3), 178.0, 76.0, 47.0, 17.0, 8, 58, 48, 15.2,
                     scenario="infection"),
    AstronautProfile("Valeria Mora", "valeria.mora@spaceoddity.dev", Sexo.FEMENINO,
                     date(1991, 1, 27), 170.0, 64.0, 35.0, 23.0, 5, 60, 52, 13.5,
                     scenario="circadian", cycle_offset_days=17),
    AstronautProfile("Daniel Vargas", "daniel.vargas@spaceoddity.dev", Sexo.MASCULINO,
                     date(1983, 6, 18), 182.0, 82.0, 48.0, 19.0, 9, 64, 38, 14.9,
                     scenario="atrophy"),
)

DOCTORS: tuple[tuple[str, str], ...] = (
    ("Dra. Laura Jiménez", "laura.jimenez@spaceoddity.dev"),
    ("Dr. Esteban Quesada", "esteban.quesada@spaceoddity.dev"),
)
ADMIN: tuple[str, str] = ("Administración Space Oddity", "admin@spaceoddity.dev")

