# Future: Grafana Dashboards

## Status: DEFERRED

This document captures the vision for Grafana dashboards, informed by the garmin-grafana project. The schema is designed to support these queries from day one.

## Dashboard Vision

Based on the garmin-grafana project (62 panels), the dashboards will cover four areas:

### 1. Intraday Health (current day / short range)

| Panel | Data Source | Query Pattern |
|---|---|---|
| Heart Rate time series | `heart_rate_intraday` | `SELECT timestamp, heart_rate WHERE user_id = $1 AND timestamp BETWEEN $2 AND $3` |
| HR Distribution histogram | `heart_rate_intraday` | Bucket count by heart_rate ranges |
| Stress time series | `stress_intraday` | `SELECT timestamp, stress_level WHERE ...` |
| Steps cumulative (24h) | `steps_intraday` | Running sum of steps by hour |
| Breathing rates | `respiration_intraday` | `SELECT timestamp, breathing_rate WHERE ...` |
| Sleep stages timeline | `sleep_intraday` | `SELECT timestamp, sleep_stage WHERE ...` |
| Sleep intraday overlay | `sleep_intraday` | SpO2, respiration, HR, stress, body battery, HRV during sleep |
| Gauges | `daily_summaries` | Resting HR, Steps, SpO2, Sleep Hours, High Stress Duration |

### 2. Recent Activities

| Panel | Data Source |
|---|---|
| GPS Track by velocity | `activity_tracks` (lat/lon + speed) |
| GPS Track by heart rate | `activity_tracks` (lat/lon + heart_rate) |
| Activity metrics over time | `activity_tracks` (HR, cadence, pace, altitude) |
| HR zones pie chart | `activity_hr_zones` |
| Recent activity table | `activities` |

### 3. Long Term Trends

| Panel | Data Source |
|---|---|
| Daily steps, distance, calories | `daily_summaries` |
| Avg health metrics time series | `daily_summaries` (RHR, SpO2, stress, body battery) |
| Race predictions | `race_predictions` |
| VO2 Max | `vo2_max` |
| Sleep stages / score | `sleep_summaries` |
| Weight | `body_compositions` |
| Training status | `training_statuses` |

### 4. Calendar / Heatmap Views

| Panel | Data Source |
|---|---|
| Week/Month at a glance | `daily_summaries` aggregated |
| HR Histogram Heatmap | `heart_rate_intraday` bucketed by hour/day |
| Hourly Walk Heatmap | `steps_intraday` by hour-of-day vs day |
| Sleep Regularity heatmap | `sleep_summaries` start/end times |
| Activity Heatmap | `activity_tracks` all GPS points |

## Postgres + Grafana Setup

Grafana's native PostgreSQL data source plugin supports all needed query patterns. Key setup:

1. **Read-only Grafana user** — Create a Postgres user with SELECT-only permissions on all tables.
2. **Time-zone handling** — Store all timestamps as TIMESTAMPTZ (UTC). Grafana handles timezone conversion in the UI.
3. **Materialized views** — For expensive aggregation queries (daily averages over long periods), create materialized views refreshed after each ingestion run.

## Schema Decisions Supporting Grafana

| Decision | Why |
|---|---|
| All timestamps as TIMESTAMPTZ | Grafana's time-range filters work natively |
| Separate intraday tables | Enables per-second/minute queries without filtering a massive combined table |
| `daily_summaries` pre-aggregated | Grafana dashboards query this directly instead of aggregating intraday data |
| `activity_tracks` with lat/lon | Geomap panel needs point geometries |
| JSONB `dynamics` column | Running/cycling dynamics are optional and vary by activity type |
| Unique constraints for upserts | Also serve as efficient index targets for Grafana queries |

## Setup (Future)

Add to `compose.yaml`:

```yaml
services:
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - grafana-data:/var/lib/grafana
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: admin
```

Dashboards will be provisioned as JSON files or built via Terraform/Grafana API.
