# garmin-postgres

Archives all available Garmin Connect health and fitness data into PostgreSQL on an ongoing basis.

## Setup

```bash
# Install dependencies (requires uv)
uv sync

# Copy and edit environment config
cp .env.sample .env

# Start the database
podman compose up -d

# Run migrations
uv run alembic upgrade head
```

## Authentication

Login interactively with your Garmin Connect credentials. OAuth tokens are stored in the database — no password is persisted.

```bash
uv run garmin-postgres auth login
```

You'll be prompted for your email and password (hidden input). If your account has MFA enabled, you'll also be asked for a verification code.

To skip the email prompt:

```bash
uv run garmin-postgres auth login --email you@example.com
```

Check authentication status for all stored users:

```bash
uv run garmin-postgres auth status
```

## Running Tests

```bash
# Unit tests only (no database required)
uv run pytest -k "not session"

# All tests (requires Podman for testcontainers)
uv run pytest
```

## CLI Commands

```bash
uv run garmin-postgres --help
uv run garmin-postgres auth --help
```
