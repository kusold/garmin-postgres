from datetime import date, timedelta

import typer
from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.db import get_engine
from garmin_postgres.models.user import User

app = typer.Typer(name="garmin-postgres", help="Archive Garmin Connect data into PostgreSQL.")
auth_app = typer.Typer(name="auth", help="Authentication commands.")
ingest_app = typer.Typer(name="ingest", help="Data ingestion commands.")
app.add_typer(auth_app, name="auth")
app.add_typer(ingest_app, name="ingest")


def _ensure_db_ready() -> None:
    engine = get_engine()
    try:
        with Session(engine) as session:
            session.connection()
    except Exception as e:
        typer.echo(f"Cannot connect to database: {e}", err=True)
        raise typer.Exit(1)

    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config()
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    alembic_cfg.set_main_option("script_location", "alembic")
    command.upgrade(alembic_cfg, "head")


@app.callback()
def main() -> None:
    """Archive Garmin Connect data into PostgreSQL."""


@auth_app.command()
def login(
    email: str = typer.Option(None, "--email", "-e", help="Garmin account email"),
) -> None:
    """Login to Garmin Connect and store tokens."""
    from garmin_postgres.auth import login_interactive, upsert_user

    _ensure_db_ready()

    garmin = login_interactive(email)
    engine = get_engine()
    with Session(engine) as session:
        user = upsert_user(session, garmin)
        full_name = user.raw_json.get("fullName", user.garmin_display_name) if user.raw_json else user.garmin_display_name
    typer.echo(f"Authenticated as {full_name}")


@auth_app.command()
def status() -> None:
    """Show authentication status for all users."""
    from garmin_postgres.auth import refresh_tokens

    _ensure_db_ready()

    engine = get_engine()
    with Session(engine) as session:
        users = session.scalars(select(User)).all()
        if not users:
            typer.echo("No users found. Run 'garmin-postgres auth login' to add a user.")
            return

        for user in users:
            name = user.raw_json.get("fullName", user.garmin_display_name) if user.raw_json else user.garmin_display_name
            active = "active" if user.is_active else "inactive"
            has_tokens = "present" if user.tokens_json else "missing"
            last_ingest = str(user.last_ingest_at) if user.last_ingest_at else "never"
            typer.echo(
                f"  {name}: {active}, tokens {has_tokens}, "
                f"last ingest: {last_ingest}"
            )


@ingest_app.command()
def run(
    user: str = typer.Option(None, "--user", "-u", help="Only ingest for this display name"),
    days_back: int = typer.Option(None, "--days-back", "-d", help="Days to look back (default: from config)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch data but don't write to DB"),
) -> None:
    """Run incremental ingestion for all active users."""
    from garmin_postgres.ingest.pipeline import run_for_all_users

    _ensure_db_ready()

    engine = get_engine()
    with Session(engine) as session:
        results = run_for_all_users(
            session,
            days_back=days_back,
            user_filter=user,
            dry_run=dry_run,
        )

    for result in results:
        name = result.pop("user")
        for data_type, info in result.items():
            typer.echo(f"  {name} / {data_type}: {info}")


@ingest_app.command()
def backfill(
    user: str = typer.Option(None, "--user", "-u", help="Only backfill for this display name"),
    days: int = typer.Option(365, "--days", "-d", help="Number of days to backfill"),
    start_date: str = typer.Option(None, "--start-date", help="Explicit start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(None, "--end-date", help="Explicit end date (YYYY-MM-DD)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch data but don't write to DB"),
) -> None:
    """Run historical backfill for all active users."""
    from garmin_postgres.ingest.pipeline import run_for_all_users

    _ensure_db_ready()

    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None

    engine = get_engine()
    with Session(engine) as session:
        results = run_for_all_users(
            session,
            start_date=parsed_start,
            end_date=parsed_end,
            days_back=days if not start_date else None,
            user_filter=user,
            dry_run=dry_run,
        )

    for result in results:
        name = result.pop("user")
        for data_type, info in result.items():
            typer.echo(f"  {name} / {data_type}: {info}")


if __name__ == "__main__":
    app()
