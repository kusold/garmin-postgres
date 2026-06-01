# Testing Strategy

## Overview

All tests use a real PostgreSQL database (via testcontainers/podman). No mocks for the database layer. This ensures SQLModel definitions, Alembic migrations, and upsert logic are validated against real Postgres behavior.

## Test Infrastructure

### Test Database

Tests spin up a PostgreSQL container using `testcontainers-postgres` (which works with podman). The container:

1. Starts a fresh Postgres instance
2. Creates the schema via Alembic migrations (tests the actual migration path)
3. Is torn down after the test session

### `conftest.py` Fixtures

```python
@pytest.fixture(scope="session")
def postgres_container():
    """Start a Postgres container for the test session."""
    ...

@pytest.fixture(scope="session")
def engine(postgres_container):
    """Create a SQLModel engine connected to the test container."""
    ...

@pytest.fixture(scope="session")
def tables(engine):
    """Run Alembic migrations to create all tables."""
    ...

@pytest.fixture
def session(tables, engine):
    """Provide a clean session with a transaction rollback after each test."""
    ...
```

Each test gets a clean database state via transaction rollback (not table truncation — faster and more reliable).

## Test Categories

### 1. Model Tests (`tests/models/`)

Verify SQLModel table definitions:
- All models have the expected columns with correct types
- Unique constraints work as expected (reject duplicates)
- Foreign key constraints are enforced
- Nullable vs required columns behave correctly

### 2. Parser Tests (`tests/parsers/`)

Pure unit tests — no database required. Each test:
1. Loads a fixture JSON file from `tests/parsers/fixtures/`
2. Calls the parser function
3. Asserts the returned SQLModel instances have correct values

Example:
```python
def test_parse_heart_rate(fixture_loader):
    raw = fixture_loader("heart_rates_2026-05-23.json")
    results = parse_heart_rate(raw, user_id=some_uuid)
    assert len(results) == 1440  # 24 hours of per-minute data
    assert results[0].heart_rate == 62
    assert results[0].timestamp == datetime(2026, 5, 23, 0, 0, tzinfo=utc)
```

### 3. Ingestion Pipeline Tests (`tests/ingest/`)

Integration tests with the real database:
- Upsert a record, then upsert again with updated values — verify the second write updated the row
- Test the date-range logic for incremental vs backfill runs
- Test that pipeline continues when one data type fails

### 4. Client Wrapper Tests (`tests/ingest/`)

Test the Garmin client wrapper with mocked API responses (using `responses` or `respx`):
- Token loading and refresh
- Retry logic on 429 responses
- Error handling for expired tokens
- Rate limiting between requests

### 5. Migration Tests (`tests/migrations/`)

- Run `alembic upgrade head` on a fresh database — verify no errors
- Run `alembic downgrade base` — verify clean teardown
- Test that auto-generated migrations match the current models (stamp check)

## Fixture Data

Sample Garmin API JSON responses stored in `tests/parsers/fixtures/`. These are captured from real Garmin API responses during development and stripped of personally identifying information.

## Coverage Target

- **Parsers**: 100% (pure functions, easy to cover)
- **Models**: 95%+ (constraint validation)
- **Pipeline**: 80%+ (error paths are hard to trigger)
- **Client wrapper**: 90%+ (mocked API responses)

## Test Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: marks integration tests requiring a database",
]
```

## Running Tests

```bash
# All tests
uv run pytest

# Fast tests only (no database)
uv run pytest tests/parsers/

# Integration tests only
uv run pytest -m integration

# With coverage
uv run pytest --cov=garmin_postgres --cov-report=html
```
