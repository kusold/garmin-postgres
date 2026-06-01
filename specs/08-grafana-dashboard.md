# Future: Grafana Dashboards

## Status: DEFERRED

This document captures the vision for Grafana dashboards. The schema is designed to support these queries from day one.

## Dashboard Vision

Dashboards will cover four areas:

1. **Intraday Health** — Heart rate, stress, steps, breathing, sleep stages, body battery
2. **Recent Activities** — GPS tracks, activity metrics, HR zones
3. **Long Term Trends** — Daily summaries, race predictions, sleep, weight, training status
4. **Calendar / Heatmap Views** — Activity heatmap, sleep regularity, hourly patterns

## Postgres + Grafana Setup

Grafana's native PostgreSQL data source plugin supports all needed query patterns. Key setup:

1. **Read-only Grafana user** — Create a Postgres user with SELECT-only permissions on all tables.
2. **Time-zone handling** — Store all timestamps as TIMESTAMPTZ (UTC). Grafana handles timezone conversion in the UI.
3. **Materialized views** — For expensive aggregation queries (daily averages over long periods), create materialized views refreshed after each ingestion run.

## Schema Decisions Supporting Grafana

| Decision | Why |
|---|---|
| All timestamps as TIMESTAMPTZ | Grafana's time-range filters work natively |
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
