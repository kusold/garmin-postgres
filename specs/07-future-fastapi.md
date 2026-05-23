# Future: FastAPI Server

## Status: DEFERRED

This document captures design considerations for when a FastAPI layer is added. No implementation now, but the architecture should not preclude it.

## Design Considerations Already Built In

### Sync + Async Engine Strategy

- The ingestion pipeline uses a sync engine (Alembic requires sync, and the cron job doesn't need async).
- When FastAPI is added, introduce an async engine (`create_async_engine` with `asyncpg` driver).
- Both engines point to the same database.
- The sync engine remains for migrations and the cron pipeline.
- The async engine serves API requests.

### Session Dependency Injection

The current `db.py` should provide a `get_session()` context manager. When FastAPI is added:

```python
# Current (sync)
def get_session():
    with Session(engine) as session:
        yield session

# Future (async)
async def get_async_session():
    async with AsyncSession(async_engine) as session:
        yield session
```

FastAPI's `Depends(get_async_session)` pattern works naturally.

### Model Separation

SQLModel models are already split into table models and can be extended with API models:

```python
# Existing table model
class Activity(ActivityBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    ...

# Future API response model
class ActivityPublic(ActivityBase):
    id: int
    user_id: int
```

### Project Structure Compatibility

The current `src/garmin_postgres/` layout separates models, ingestion, and config. Adding an `api/` directory alongside them is natural:

```
src/garmin_postgres/
├── api/          # Future: FastAPI routes
│   ├── main.py
│   ├── deps.py
│   └── routes/
├── models/       # Shared
├── ingest/       # Shared
├── config.py     # Shared
└── db.py         # Shared
```

## Future API Scope

When implemented, the API would provide:

- REST endpoints for querying ingested data (activities, daily summaries, sleep, etc.)
- User authentication (separate from Garmin auth — local JWT or API keys)
- Read-only access to archived data
- Optional: real-time ingestion trigger

## Non-Requirements

- The API should NOT expose Garmin credentials or tokens
- The API should NOT write data back to Garmin
- The API should NOT be required for the ingestion pipeline to work
