# M1 Foundation — Project scaffolding, DB config, and Alembic

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the complete Python project skeleton with database connection, session management, and Alembic migrations validated against real Postgres.

**Architecture:** uv-managed Python 3.13 project with src layout. SQLModel ORM backed by PostgreSQL 17 via Podman Compose. Alembic for schema migrations with naming conventions. Config via Pydantic Settings reading env vars.

**Tech Stack:** Python 3.13, uv, SQLModel, Alembic, PostgreSQL 17, Pydantic Settings, typer, pytest, testcontainers

---

## Vikunja Task Mapping

| Vikunja ID | Title | Plan Task |
|---|---|---|
| #30 | Create uv project with pyproject.toml and all dependencies | 1 |
| #31 | Create Podman Compose config for PostgreSQL 17 | 2 |
| #32 | Implement config.py with Pydantic Settings | 3 |
| #34 | Initialize Alembic with SQLModel integration | 4 |
| #35 | Customize script.py.mako for SQLModel types | 4 |
| #33 | Implement db.py with engine and session factory | 5 |
| #36 | Create initial empty migration and validate against real Postgres | 6 |

---

## Prerequisites

- Python 3.13 installed
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Podman running (`systemctl --user start podman.socket`)
- `DOCKER_HOST` set if using podman: `export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/podman/podman.sock"`

---

## Task 1: Initialize uv project

**Files:**
- Create: `pyproject.toml`
- Create: `src/garmin_postgres/__init__.py`
- Create: `src/garmin_postgres/cli.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "garmin-postgres"
version = "0.1.0"
description = "Archive Garmin Connect health and fitness data into PostgreSQL"
requires-python = ">=3.13"
dependencies = [
    "garminconnect>=0.2.25",
    "sqlmodel>=0.0.22",
    "alembic>=1.14.0",
    "typer>=0.15.0",
    "pydantic-settings>=2.7.0",
    "psycopg2-binary>=2.9.10",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "testcontainers[postgres]>=4.9.0",
]

[project.scripts]
garmin-postgres = "garmin_postgres.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/garmin_postgres"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"
```

- [ ] **Step 2: Create package directory structure**

```bash
mkdir -p src/garmin_postgres/models src/garmin_postgres/ingest/parsers tests
```

- [ ] **Step 3: Create `src/garmin_postgres/__init__.py`**

```python
"""garmin-postgres: Archive Garmin Connect data into PostgreSQL."""
```

- [ ] **Step 4: Create `src/garmin_postgres/cli.py`**

```python
import typer

app = typer.Typer(name="garmin-postgres", help="Archive Garmin Connect data into PostgreSQL.")

if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Install dependencies and verify**

Run: `uv sync --extra dev`
Expected: Dependencies resolve and install. Lock file created.

Run: `uv run garmin-postgres --help`
Expected: Typer help output showing no commands yet.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/
git commit -m "feat: initialize uv project with dependencies and src layout"
```

---

## Task 2: Create compose.yaml and .env.example

**Files:**
- Create: `compose.yaml`
- Create: `.env.example`

- [ ] **Step 1: Create `compose.yaml`**

```yaml
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_DB: garmin
      POSTGRES_USER: garmin
      POSTGRES_PASSWORD: garmin
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

- [ ] **Step 2: Create `.env.example`**

```
DATABASE_URL=postgresql://garmin:garmin@localhost:5432/garmin
GARMIN_TOKEN_DIR=~/.garth-tokens
LOG_LEVEL=INFO
INGEST_DAYS_BACK=1
```

- [ ] **Step 3: Verify compose config validates**

Run: `podman compose config`
Expected: YAML output with no errors.

- [ ] **Step 4: Commit**

```bash
git add compose.yaml .env.example
git commit -m "feat: add compose.yaml for PostgreSQL 17 and .env.example"
```

---

## Task 3: Implement config.py with tests (TDD)

**Files:**
- Create: `src/garmin_postgres/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Create `tests/__init__.py`**

Empty file.

- [ ] **Step 2: Write the failing test**

Create `tests/test_config.py`:

```python
import os

import pytest


def test_settings_loads_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://testuser:testpass@localhost:5432/testdb")
    from garmin_postgres.config import Settings

    settings = Settings()
    assert settings.database_url == "postgresql://testuser:testpass@localhost:5432/testdb"


def test_settings_default_log_level():
    os.environ.pop("LOG_LEVEL", None)
    from garmin_postgres.config import Settings

    settings = Settings()
    assert settings.log_level == "INFO"


def test_settings_default_ingest_days_back():
    os.environ.pop("INGEST_DAYS_BACK", None)
    from garmin_postgres.config import Settings

    settings = Settings()
    assert settings.ingest_days_back == 1


def test_get_settings_returns_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    from garmin_postgres.config import get_settings

    settings = get_settings()
    assert settings.database_url is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'garmin_postgres.config'`

- [ ] **Step 4: Implement config.py**

Create `src/garmin_postgres/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://garmin:garmin@localhost:5432/garmin"
    garmin_token_dir: str = "~/.garth-tokens"
    log_level: str = "INFO"
    ingest_days_back: int = 1


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/garmin_postgres/config.py tests/__init__.py tests/test_config.py
git commit -m "feat: add config.py with Pydantic Settings and tests"
```

---

## Task 4: Create models/base.py and Alembic setup

This task creates the shared base model (needed by Alembic's `env.py`) and initializes Alembic with SQLModel integration.

**Files:**
- Create: `src/garmin_postgres/models/__init__.py`
- Create: `src/garmin_postgres/models/base.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/.gitkeep`

- [ ] **Step 1: Create `src/garmin_postgres/models/base.py`**

```python
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, func
from sqlmodel import Field, SQLModel

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SQLModel.metadata.naming_convention = NAMING_CONVENTION


class BaseModel(SQLModel):
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
    )
```

- [ ] **Step 2: Create `src/garmin_postgres/models/__init__.py`**

```python
from garmin_postgres.models.base import BaseModel

__all__ = ["BaseModel"]
```

- [ ] **Step 3: Initialize Alembic**

```bash
uv run alembic init alembic
```

Expected: Creates `alembic/` directory with `env.py`, `script.py.mako`, `versions/`, and `alembic.ini`.

- [ ] **Step 4: Replace `alembic/env.py`**

Overwrite with:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from garmin_postgres.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: Replace `alembic/script.py.mako`**

Overwrite with:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
import sqlmodel.sql.sqltypes  # noqa: F401

${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 6: Update `alembic.ini`**

Edit `alembic.ini` — keep the default but ensure `script_location = alembic`. The `sqlalchemy.url` line is a placeholder; `env.py` overrides it from config.

- [ ] **Step 7: Add .gitkeep for versions directory**

```bash
touch alembic/versions/.gitkeep
```

- [ ] **Step 8: Verify alembic can be imported**

Run: `uv run python -c "from alembic.config import Config; c = Config('alembic.ini'); print(c.get_main_option('script_location'))"`
Expected: `alembic`

- [ ] **Step 9: Commit**

```bash
git add src/garmin_postgres/models/ alembic/ alembic.ini
git commit -m "feat: add base model with naming convention and configure Alembic with SQLModel"
```

---

## Task 5: Implement db.py with test infrastructure (TDD)

**Files:**
- Create: `src/garmin_postgres/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Create `tests/conftest.py`**

```python
import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:17") as postgres:
        os.environ["DATABASE_URL"] = postgres.get_connection_url()
        yield postgres


@pytest.fixture(scope="session")
def engine(postgres_container):
    from garmin_postgres.db import get_engine

    engine = get_engine()
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_db.py`:

```python
from sqlalchemy import text


def test_engine_connects(engine):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


def test_get_session_yields_working_session(session):
    result = session.execute(text("SELECT 1"))
    assert result.scalar() == 1


def test_session_rollback_isolation(session):
    session.execute(text("CREATE TEMP TABLE test_isolation (val int)"))
    session.execute(text("INSERT INTO test_isolation VALUES (42)"))
    result = session.execute(text("SELECT val FROM test_isolation")).scalar()
    assert result == 42
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'garmin_postgres.db'`

- [ ] **Step 4: Implement db.py**

Create `src/garmin_postgres/db.py`:

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from garmin_postgres.config import get_settings


def get_engine():
    settings = get_settings()
    return create_engine(settings.database_url)


def get_session() -> Generator[Session, None]:
    engine = get_engine()
    with Session(engine) as session:
        yield session
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/garmin_postgres/db.py tests/conftest.py tests/test_db.py
git commit -m "feat: add db.py with engine and session factory, testcontainers infrastructure"
```

---

## Task 6: Create initial migration and validate end-to-end

**Files:**
- Create: `tests/test_migrations.py`
- Create: `alembic/versions/<auto>_initial.py` (via alembic command)

- [ ] **Step 1: Write the migration validation test**

Create `tests/test_migrations.py`:

```python
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_alembic_upgrade_head(postgres_container):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_container.get_connection_url())
    command.upgrade(alembic_cfg, "head")


def test_alembic_version_stamped(postgres_container, engine):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_container.get_connection_url())
    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar()
        assert version is not None


def test_alembic_downgrade_base(postgres_container):
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_container.get_connection_url())
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
```

- [ ] **Step 2: Run test to verify it fails (no migration yet)**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: FAIL — alembic error or no migration revisions found.

- [ ] **Step 3: Create the initial empty migration**

```bash
uv run alembic revision -m "initial"
```

Expected: Creates `alembic/versions/<hash>_initial.py` with empty `upgrade()` and `downgrade()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run all tests together**

Run: `uv run pytest -v`
Expected: All tests pass (config: 4, db: 3, migrations: 3 = 10 total).

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/ tests/test_migrations.py
git commit -m "feat: add initial Alembic migration and end-to-end validation tests"
```

---

## Self-Review

### 1. Spec Coverage

| Spec Requirement | Plan Task |
|---|---|
| uv project with src layout (spec 02) | Task 1 |
| All dependencies installed (spec 01) | Task 1 |
| compose.yaml with postgres:17 (spec 02) | Task 2 |
| config.py with Pydantic Settings (spec 02) | Task 3 |
| DATABASE_URL, LOG_LEVEL, INGEST_DAYS_BACK (spec 02) | Task 3 |
| db.py with engine + session factory (spec 02) | Task 5 |
| BaseModel with id/created_at/updated_at (spec 03) | Task 4 |
| BIGINT auto-increment PKs (spec 03) | Task 4 |
| Naming convention on metadata (spec 03) | Task 4 |
| Alembic env.py imports all models (CLAUDE.md) | Task 4 |
| target_metadata = SQLModel.metadata (CLAUDE.md) | Task 4 |
| compare_type=True (CLAUDE.md) | Task 4 |
| script.py.mako includes sqlmodel.sql.sqltypes (CLAUDE.md) | Task 4 |
| Test infrastructure with testcontainers (spec 06) | Task 5 |
| Migration validation tests (spec 06) | Task 6 |
| TIMESTAMPTZ for all timestamps (spec 03) | Task 4 |

No gaps found.

### 2. Placeholder Scan

No TBDs, TODOs, or "implement later" patterns found. All steps contain complete code.

### 3. Type Consistency

- `Settings.database_url` used in `db.py` and `alembic/env.py` — consistent.
- `BaseModel.id` is `int | None` with `BigInteger` Column — consistent across all future table models.
- `get_engine()` returns a `sqlalchemy.Engine` — used consistently in `db.py` and `conftest.py`.
- `get_session()` yields `Session` — used consistently in `db.py`.
