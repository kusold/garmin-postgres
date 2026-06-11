# garmin-to-notion Reference

Reference for [chloevoyer/garmin-to-notion](https://github.com/chloevoyer/garmin-to-notion) — a project that syncs Garmin Connect data to Notion databases. Useful for identifying Garmin data types and API endpoints we may want to ingest.

## Architecture

- Python scripts in `src/workflows/`, each run independently (activities, personal records, daily steps, sleep data)
- GitHub Actions workflow for automated sync
- Uses `python-garminconnect` (token-based auth since March 2025)
- Writes to separate Notion databases via the Notion API

## Data Types Fetched

### Activities (`src/workflows/garmin-activities.py`)

| API Method | Data |
|---|---|
| `get_activities` | Activity list — name, type, time, distance, duration, calories, HR, pace, elevation |
| `get_activity` | Individual activity details |
| `get_activity_details` | Splits, chart data (elevation/HR/pace), GPS polylines |

### Daily Health (`src/workflows/daily-steps.py`)

| API Method | Data |
|---|---|
| `get_user_summary` / `get_stats` | Steps, calories, resting HR, stress, body battery, intensity minutes |

### Sleep (`src/workflows/sleep-data.py`)

| API Method | Data |
|---|---|
| `get_sleep_data` | Sleep stages (deep/light/REM/awake), duration, score, SpO2, HRV, respiration |

### Not fetched but noted in `python-garminconnect` docs

These endpoints are available in the library but not used by garmin-to-notion:

| API Method | Data |
|---|---|
| `get_heart_rates` | Intraday HR time-series, min/max/resting |
| `get_stress_data` / `get_all_day_stress` | Average/max stress, stress/rest duration |
| `get_body_composition` / `get_weight` | Weight, body fat %, BMI, muscle mass, bone mass, visceral fat |
| `get_hydration_data` | Daily water intake, hydration goals |
| `get_training_readiness` | Readiness score (0-100), recovery time, sleep/HRV/ACWR factors |
| `get_training_status` | Status phrase, VO2max trends, training load |
| `get_max_metrics` | VO2max values, fitness age, heat/altitude acclimation |
| `get_spo2_data` | Average/lowest blood oxygen saturation |
| `get_respiration_data` | Waking/sleep respiration rates, time-series |
| `get_intensity_minutes_data` | Moderate & vigorous intensity minutes |

## Personal Records (`src/workflows/personal-records.py`)

Personal records are **not calculated** — Garmin Connect computes them server-side. The script calls `garmin_client.get_personal_record()` and syncs the results to Notion.

### Record types (`typeId`)

| typeId | Record | Value Format |
|--------|--------|-------------|
| 1 | Fastest 1K | `M:SS /km` pace |
| 2 | Fastest 1 mile | `M:SS` (pace converted to `/km`) |
| 3 | Fastest 5K | `M:SS` (pace `/km`) |
| 4 | Fastest 10K | `H:MM:SS` (pace `/km`) |
| 7 | Longest Run | distance in km |
| 8 | Longest Ride | distance in km |
| 9 | Total Ascent | meters |
| 10 | Max Avg Power (20 min) | watts |
| 12 | Most Steps in a Day | count |
| 13 | Most Steps in a Week | count |
| 14 | Most Steps in a Month | count |
| 15 | Longest Goal Streak | days |
| 16 | *(filtered out)* | — |

Each record includes: `value`, `prStartTimeGmtFormatted` (date), `activityType`, `typeId`.

### PR update logic

1. Fetch all records from Garmin
2. For each record, check if a Notion entry already exists for that date + name
3. If existing PR found and new date is newer: archive old PR (`PR: false`), write new one
4. If no existing PR: write new record

## Garmin API Endpoints

Reference endpoints used or available:

```
# Activities
/activity-service/activity/{activityId}

# Daily summary
/usersummary-service/usersummary/daily/{displayName}

# Heart rate
/wellness-service/heart-rate/date/{date}

# Sleep
/wellness-service/sleep/date/{date}

# Stress
/wellness-service/wellness/dailyStress/{date}

# Body battery
/wellness-service/wellness/bodyBattery/reports/daily

# HRV
/hrv-service/hrv/{date}

# Training readiness
/metrics-service/metrics/trainingreadiness/{date}

# Training status
/metrics-service/metrics/trainingstatus/aggregated/{date}

# VO2max
/metrics-service/metrics/maxmet/daily/{date}/{date}

# Intensity minutes
/wellness-service/wellness/daily/im/{date}

# Body composition
/bodyComposition

# Weight
/weighIn

# Hydration
/wellness-service/hydration/{date}

# Personal records (exact endpoint unknown — handled by python-garminconnect)
```

## Gap Analysis vs garmin-postgres

**Currently implemented in garmin-postgres:**
- Daily summary (`get_user_summary`)
- User profile

**Missing from garmin-postgres (in priority order):**

1. **Activities** — the core fitness data (runs, rides, swims, etc.)
2. **Sleep data** — stages, duration, score
3. **Heart rate (intraday)** — time-series HR samples
4. **Personal records** — trivial to add since Garmin computes them
5. **Stress data** — daily stress levels
6. **Body composition** — weight, body fat trends
7. **HRV** — heart rate variability
8. **Training readiness** — readiness scores
9. **Training status** — VO2max trends, training load
10. **VO2max / max metrics** — fitness age, acclimation
11. **SpO2** — blood oxygen saturation
12. **Respiration** — breathing rate data
13. **Hydration** — water intake tracking
14. **Intensity minutes** — moderate/vigorous minutes
