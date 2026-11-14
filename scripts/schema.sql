PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================================
-- 1. USERS & MISSIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    role INTEGER NOT NULL CHECK (role IN (0, 1, 2)),      -- 0=Astronaut, 1=Doctor, 2=Admin
    birth_date TEXT NOT NULL,                             -- ISO YYYY-MM-DD
    sex INTEGER NOT NULL CHECK (sex IN (0, 1))            -- 0=Female, 1=Male
);

CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    radiation_limit_msv REAL NOT NULL DEFAULT 600.0,
    start_date TEXT NOT NULL,                             -- ISO YYYY-MM-DD
    end_date TEXT
);

CREATE TABLE IF NOT EXISTS mission_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_user INTEGER NOT NULL,
    id_mission INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT,
    start_weight_kg REAL NOT NULL,
    start_height_cm REAL NOT NULL,
    start_muscle_pct REAL NOT NULL,
    start_fat_pct REAL NOT NULL,
    baseline_hb_g_dl REAL DEFAULT 14.5,
    active_monitoring_mode TEXT NOT NULL DEFAULT 'standard'
        CHECK (active_monitoring_mode IN ('standard', 'alert_infection', 'alert_atrophy', 'alert_circadian')),
    FOREIGN KEY (id_user) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (id_mission) REFERENCES missions(id) ON DELETE CASCADE
);

-- ============================================================================
-- 2. FIXED SCHEDULES (PILLS & MEALS) + DYNAMIC REMINDERS LOG
-- ============================================================================
CREATE TABLE IF NOT EXISTS pills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_user INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    scheduled_time TEXT NOT NULL,                         -- HH:MM
    frequency_days INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (id_user) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_user INTEGER NOT NULL,
    name TEXT NOT NULL,
    meal_type TEXT NOT NULL CHECK (meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')),
    target_calories_kcal REAL,
    target_protein_g REAL,
    scheduled_time TEXT NOT NULL,                         -- HH:MM
    frequency_days INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (id_user) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reminders_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mission_log INTEGER NOT NULL,
    reminder_type TEXT NOT NULL
        CHECK (reminder_type IN ('pill', 'meal', 'glucose_sample', 'saliva_sample', 'bia_weigh_in')),
    id_pill INTEGER,
    id_meal INTEGER,
    title TEXT NOT NULL,
    instructions TEXT,
    monitoring_mode TEXT NOT NULL DEFAULT 'standard'
        CHECK (monitoring_mode IN ('standard', 'alert_infection', 'alert_atrophy', 'alert_circadian')),
    mission_day INTEGER NOT NULL,
    scheduled_at TEXT NOT NULL,                           -- ISO YYYY-MM-DDTHH:MM:SS
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed', 'skipped')),
    completed_at TEXT,
    intake_pct REAL DEFAULT 100.0,
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),
    FOREIGN KEY (id_mission_log) REFERENCES mission_logs(id) ON DELETE CASCADE,
    FOREIGN KEY (id_pill) REFERENCES pills(id) ON DELETE SET NULL,
    FOREIGN KEY (id_meal) REFERENCES meals(id) ON DELETE SET NULL
);

-- ============================================================================
-- 3. BIOMETRIC & CLINICAL MEASUREMENTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS cardiovascular_autonomic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mission_log INTEGER NOT NULL,
    mission_day INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,                            -- ISO timestamp (15-min blocks)
    heart_rate_bpm REAL NOT NULL,
    hrv_rmssd_ms REAL NOT NULL,
    hrv_sdnn_ms REAL NOT NULL,
    rr_mean_ms REAL NOT NULL,
    rr_intervals_sample_json TEXT,                        -- Optional JSON array of RR intervals (ms)
    estimated_systolic_bp_mmhg REAL,
    estimated_diastolic_bp_mmhg REAL,
    estimated_map_mmhg REAL,
    spo2_pct REAL NOT NULL,
    perfusion_index_pct REAL,
    respiratory_rate_rpm REAL NOT NULL,
    skin_temperature_c REAL NOT NULL,
    skin_temp_deviation_c REAL,
    sleep_stage INTEGER NOT NULL CHECK (sleep_stage IN (0, 1, 2, 3)), -- 0=Awake, 1=Light, 2=Deep, 3=REM
    enmo_mg REAL NOT NULL,                                -- Accelerometry ENMO (milli-g)
    activity_intensity_level INTEGER NOT NULL CHECK (activity_intensity_level IN (0, 1, 2, 3)), -- 0=Rest..3=Vigorous
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),
    FOREIGN KEY (id_mission_log) REFERENCES mission_logs(id) ON DELETE CASCADE,
    UNIQUE (id_mission_log, recorded_at, is_simulated)
);

CREATE TABLE IF NOT EXISTS blood_measures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mission_log INTEGER NOT NULL,
    mission_day INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    blood_glucose_mg_dl REAL,
    measurement_context TEXT NOT NULL DEFAULT 'fasting'
        CHECK (measurement_context IN ('fasting', 'post_meal_2h', 'random', 'alert_hourly')),
    hemoglobin_g_dl REAL,                                 -- Optional for DO2 & space anemia
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),
    FOREIGN KEY (id_mission_log) REFERENCES mission_logs(id) ON DELETE CASCADE,
    CHECK (blood_glucose_mg_dl IS NOT NULL OR hemoglobin_g_dl IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS saliva_measures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mission_log INTEGER NOT NULL,
    mission_day INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    collection_slot TEXT NOT NULL
        CHECK (collection_slot IN ('wake_0m', 'wake_30m', 'afternoon', 'bedtime')),
    sample_volume_ul INTEGER,
    salivary_cortisol_nmol_l REAL NOT NULL,
    salivary_dhea_pg_ml REAL,
    salivary_dhea_nmol_l REAL NOT NULL,
    salivary_alpha_amylase_u_ml REAL NOT NULL,
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),
    FOREIGN KEY (id_mission_log) REFERENCES mission_logs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS body_composition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mission_log INTEGER NOT NULL,
    mission_day INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    visceral_fat_level REAL NOT NULL,
    body_water_pct REAL,
    muscle_pct REAL NOT NULL,
    fat_pct REAL,
    is_fasting INTEGER NOT NULL DEFAULT 1 CHECK (is_fasting IN (0, 1)),
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),
    FOREIGN KEY (id_mission_log) REFERENCES mission_logs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS menstrual_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_user INTEGER NOT NULL,
    recorded_date TEXT NOT NULL,                          -- ISO YYYY-MM-DD
    menstruating INTEGER NOT NULL CHECK (menstruating IN (0, 1)),
    cycle_phase TEXT CHECK (cycle_phase IN ('menstrual', 'follicular', 'ovulatory', 'luteal')),
    FOREIGN KEY (id_user) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (id_user, recorded_date)
);

-- ============================================================================
-- 4. SPACE RADIATION ENVIRONMENT
-- ============================================================================
CREATE TABLE IF NOT EXISTS radiation_measures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mission_log INTEGER NOT NULL,
    mission_day INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    daily_dose_msv REAL NOT NULL,
    cumulative_dose_msv REAL NOT NULL,
    weighted_dose_msv REAL NOT NULL,
    solar_event_flag INTEGER NOT NULL DEFAULT 0 CHECK (solar_event_flag IN (0, 1)),
    solar_event_type TEXT,
    data_source TEXT NOT NULL DEFAULT 'synthetic_baseline'
        CHECK (data_source IN ('synthetic_baseline', 'nasa_donki', 'noaa_swpc', 'simulated')),
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),
    FOREIGN KEY (id_mission_log) REFERENCES mission_logs(id) ON DELETE CASCADE
);

-- ============================================================================
-- 5. DAILY HEALTH REPORTS (SLEEP SUMMARY + 17 MODELS + READINESS SCORE)
-- ============================================================================
CREATE TABLE IF NOT EXISTS daily_health_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mission_log INTEGER NOT NULL,
    mission_day INTEGER NOT NULL,
    report_date TEXT NOT NULL,                            -- ISO YYYY-MM-DD

    -- Sleep architecture summary
    sleep_total_min INTEGER NOT NULL,
    sleep_awake_min INTEGER NOT NULL,
    sleep_light_min INTEGER NOT NULL,
    sleep_deep_min INTEGER NOT NULL,
    sleep_rem_min INTEGER NOT NULL,
    sleep_efficiency_pct REAL NOT NULL,

    -- Cardiovascular, autonomic & accelerometry
    resting_hr_bpm REAL,
    hrv_rmssd_daily_ms REAL,
    hrv_sdnn_daily_ms REAL,
    hrv_baseline_28d_ms REAL,
    hrv_z_score REAL,
    nocturnal_skin_temp_c REAL,
    skin_temp_baseline_28d_c REAL,
    skin_temp_delta_c REAL,
    respiratory_rate_daily_rpm REAL,
    spo2_daily_pct REAL,
    enmo_daily_mean_mg REAL,
    sedentary_min INTEGER,
    light_activity_min INTEGER,
    mvpa_min INTEGER,

    -- Anthropometry, sarcopenia & GLIM malnutrition
    bmi REAL,
    delta_mass_pct REAL,
    smi_total_kg_m2 REAL,
    bmr_mifflin_kcal REAL,
    glim_malnutrition_risk INTEGER DEFAULT 0 CHECK (glim_malnutrition_risk IN (0, 1, 2)),

    -- HPA axis & immunosuppression (nullable on days without saliva)
    car_absolute_nmol_l REAL,
    car_pct REAL,
    cortisol_diurnal_slope REAL,
    aucg_cortisol REAL,
    aucg_saa REAL,
    sns_hpa_asymmetry_idx REAL,
    cortisol_dhea_molar_ratio REAL,

    -- Glycemic variability (nullable on days without glucose)
    glucose_cv_pct REAL,
    glucose_tir_pct REAL,
    gmi_pct REAL,

    -- Liebermeister, DO2 & Radiation
    liebermeister_dissociation_flag INTEGER DEFAULT 0 CHECK (liebermeister_dissociation_flag IN (0, 1)),
    do2_ml_min REAL,
    anemia_suspicion_flag INTEGER DEFAULT 0 CHECK (anemia_suspicion_flag IN (0, 1)),
    cumulative_radiation_msv REAL,

    -- Operational readiness & adaptive sampling
    readiness_score REAL NOT NULL CHECK (readiness_score BETWEEN 0.0 AND 100.0),
    triage_status TEXT NOT NULL CHECK (triage_status IN ('green', 'yellow', 'red')),
    recommended_sampling_mode TEXT NOT NULL DEFAULT 'standard'
        CHECK (recommended_sampling_mode IN ('standard', 'alert_infection', 'alert_atrophy', 'alert_circadian')),
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),

    FOREIGN KEY (id_mission_log) REFERENCES mission_logs(id) ON DELETE CASCADE,
    UNIQUE (id_mission_log, mission_day, is_simulated)
);

-- ============================================================================
-- 6. CLINICAL ALERTS & ACTION PLANS
-- ============================================================================
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mission_log INTEGER NOT NULL,
    mission_day INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    alert_category TEXT NOT NULL
        CHECK (alert_category IN (
            'sepsis_infection', 'atrophy_glim', 'hpa_circadian',
            'cardiovascular', 'glycemic', 'radiation', 'anemia_oxygen'
        )),
    severity TEXT NOT NULL CHECK (severity IN ('yellow', 'orange', 'red')),
    title TEXT NOT NULL,
    triggered_rules_json TEXT NOT NULL,
    action_plan_text TEXT NOT NULL,
    infographic_asset TEXT,
    sampling_mode_triggered TEXT
        CHECK (sampling_mode_triggered IN ('standard', 'alert_infection', 'alert_atrophy', 'alert_circadian')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'acknowledged', 'resolved')),
    email_sent INTEGER NOT NULL DEFAULT 0 CHECK (email_sent IN (0, 1)),
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),
    FOREIGN KEY (id_mission_log) REFERENCES mission_logs(id) ON DELETE CASCADE
);

-- ============================================================================
-- 7. PERFORMANCE INDEXES (TRENDS, 28D BASELINES & SIMULATOR RESET)
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_ca_log_day ON cardiovascular_autonomic(id_mission_log, mission_day);
CREATE INDEX IF NOT EXISTS idx_ca_sim ON cardiovascular_autonomic(is_simulated);

CREATE INDEX IF NOT EXISTS idx_bm_log_day ON blood_measures(id_mission_log, mission_day);
CREATE INDEX IF NOT EXISTS idx_bm_sim ON blood_measures(is_simulated);

CREATE INDEX IF NOT EXISTS idx_sm_log_day ON saliva_measures(id_mission_log, mission_day);
CREATE INDEX IF NOT EXISTS idx_sm_sim ON saliva_measures(is_simulated);

CREATE INDEX IF NOT EXISTS idx_bc_log_day ON body_composition(id_mission_log, mission_day);
CREATE INDEX IF NOT EXISTS idx_bc_sim ON body_composition(is_simulated);

CREATE INDEX IF NOT EXISTS idx_rm_log_day ON radiation_measures(id_mission_log, mission_day);
CREATE INDEX IF NOT EXISTS idx_rm_sim ON radiation_measures(is_simulated);

CREATE INDEX IF NOT EXISTS idx_dhr_log_day ON daily_health_reports(id_mission_log, mission_day);
CREATE INDEX IF NOT EXISTS idx_dhr_sim ON daily_health_reports(is_simulated);

CREATE INDEX IF NOT EXISTS idx_alerts_log_status ON alerts(id_mission_log, status, is_simulated);
CREATE INDEX IF NOT EXISTS idx_rem_log_day_status ON reminders_log(id_mission_log, mission_day, status, is_simulated);
