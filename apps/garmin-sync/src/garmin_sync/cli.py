from contextlib import nullcontext
from datetime import date
from importlib.resources import as_file, files
from pathlib import Path

import typer
from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.db import get_engine
from garmin_postgres.models.user import User
from garmin_sync.ingest.date_windows import parse_date
from garmin_sync.ingest.object_registry import UnknownIngestObject, normalize_data_types

app = typer.Typer(name="garmin-sync", help="Archive Garmin Connect data into PostgreSQL.")
auth_app = typer.Typer(name="auth", help="Authentication commands.")
ingest_app = typer.Typer(name="ingest", help="Data ingestion commands.")
app.add_typer(auth_app, name="auth")
app.add_typer(ingest_app, name="ingest")


def _alembic_script_location():
    packaged_location = files("garmin_postgres").joinpath("alembic")
    if packaged_location.is_dir():
        return packaged_location

    raise FileNotFoundError("Could not find packaged Alembic migrations")


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
    script_location = _alembic_script_location()
    script_location_context = (
        nullcontext(script_location)
        if isinstance(script_location, Path)
        else as_file(script_location)
    )
    with script_location_context as location:
        alembic_cfg.set_main_option("script_location", str(location))
        command.upgrade(alembic_cfg, "head")


@app.callback()
def main() -> None:
    """Archive Garmin Connect data into PostgreSQL."""


@auth_app.command()
def login(
    email: str = typer.Option(None, "--email", "-e", help="Garmin account email"),
) -> None:
    """Login to Garmin Connect and store tokens."""
    from garmin_sync.auth import login_interactive, upsert_user

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
    from garmin_sync.auth import refresh_tokens

    _ensure_db_ready()

    engine = get_engine()
    with Session(engine) as session:
        users = session.scalars(select(User)).all()
        if not users:
            typer.echo("No users found. Run 'garmin-sync auth login' to add a user.")
            return

        for user in users:
            name = user.raw_json.get("fullName", user.garmin_display_name) if user.raw_json else user.garmin_display_name
            tokens_valid = refresh_tokens(session, user) if user.tokens_json else False
            active = "active" if user.is_active and tokens_valid else "inactive"
            token_status = "valid" if tokens_valid else ("missing" if not user.tokens_json else "invalid")
            last_ingest = str(user.last_ingest_at) if user.last_ingest_at else "never"
            typer.echo(
                f"  {name}: {active}, tokens {token_status}, "
                f"last ingest: {last_ingest}"
            )


def _parse_date_option(value: str | None, option_name: str) -> date | None:
    try:
        return parse_date(value)
    except ValueError:
        typer.echo(f"{option_name} must be in YYYY-MM-DD format", err=True)
        raise typer.Exit(1)


def _print_results(results: list[dict]) -> None:
    for result in results:
        name = result["user"]
        for data_type, info in result.items():
            if data_type == "user":
                continue
            typer.echo(f"  {name} / {data_type}: {info}")


def _parse_data_type_options(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    try:
        return normalize_data_types(values)
    except UnknownIngestObject as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)


def _exit_for_failed_results(
    results: list[dict],
    *,
    fail_on_partial: bool = False,
) -> None:
    statuses = [
        info.get("status")
        for result in results
        for data_type, info in result.items()
        if data_type != "user"
    ]
    if "error" in statuses or (fail_on_partial and "partial" in statuses):
        raise typer.Exit(1)


@ingest_app.command()
def run(
    user: str = typer.Option(None, "--user", "-u", help="Only ingest for this display name"),
    days_back: int = typer.Option(None, "--days-back", "-d", help="Days to look back (default: from config)"),
    data_type: list[str] = typer.Option(None, "--data-type", "-t", help="Data types to ingest (daily-summary, activities, personal-records)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch data but don't write to DB"),
    fail_on_partial: bool = typer.Option(False, "--fail-on-partial", help="Exit non-zero when any selected object is partial"),
) -> None:
    """Run incremental ingestion for all active users."""
    if days_back is not None and days_back < 1:
        typer.echo("--days-back must be at least 1", err=True)
        raise typer.Exit(1)
    parsed_data_types = _parse_data_type_options(data_type)

    from garmin_sync.ingest.pipeline import run_for_all_users

    _ensure_db_ready()

    engine = get_engine()
    with Session(engine) as session:
        results = run_for_all_users(
            session,
            days_back=days_back,
            user_filter=user,
            dry_run=dry_run,
            data_types=parsed_data_types,
        )

    _print_results(results)
    _exit_for_failed_results(results, fail_on_partial=fail_on_partial)


@ingest_app.command()
def backfill(
    user: str = typer.Option(None, "--user", "-u", help="Only backfill for this display name"),
    days: int = typer.Option(365, "--days", "--days-back", "-d", help="Number of days to backfill"),
    start_date: str = typer.Option(None, "--start-date", help="Explicit start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(None, "--end-date", help="Explicit end date (YYYY-MM-DD)"),
    data_type: list[str] = typer.Option(None, "--data-type", "-t", help="Data types to ingest (daily-summary, activities, personal-records)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch data but don't write to DB"),
    fail_on_partial: bool = typer.Option(False, "--fail-on-partial", help="Exit non-zero when any selected object is partial"),
) -> None:
    """Run historical backfill for all active users."""
    from garmin_sync.ingest.pipeline import run_for_all_users

    _ensure_db_ready()

    parsed_data_types = _parse_data_type_options(data_type)
    parsed_start = _parse_date_option(start_date, "--start-date")
    parsed_end = _parse_date_option(end_date, "--end-date")

    engine = get_engine()
    with Session(engine) as session:
        results = run_for_all_users(
            session,
            start_date=parsed_start,
            end_date=parsed_end,
            days_back=days if not start_date else None,
            user_filter=user,
            dry_run=dry_run,
            data_types=parsed_data_types,
        )

    _print_results(results)
    _exit_for_failed_results(results, fail_on_partial=fail_on_partial)


@ingest_app.command("daily-summary")
def daily_summary(
    user: str = typer.Option(None, "--user", "-u", help="Only ingest for this display name"),
    days_back: int = typer.Option(None, "--days-back", "-d", help="Days to look back (default: from config)"),
    start_date: str = typer.Option(None, "--start-date", help="Explicit start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(None, "--end-date", help="Explicit end date (YYYY-MM-DD)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch data but don't write to DB"),
    fail_on_partial: bool = typer.Option(False, "--fail-on-partial", help="Exit non-zero when any selected object is partial"),
) -> None:
    """Ingest daily summaries."""
    if days_back is not None and days_back < 1:
        typer.echo("--days-back must be at least 1", err=True)
        raise typer.Exit(1)

    from garmin_sync.ingest.pipeline import run_for_all_users

    _ensure_db_ready()
    engine = get_engine()
    with Session(engine) as session:
        results = run_for_all_users(
            session,
            start_date=_parse_date_option(start_date, "--start-date"),
            end_date=_parse_date_option(end_date, "--end-date"),
            days_back=days_back,
            user_filter=user,
            dry_run=dry_run,
            data_types=["daily_summary"],
        )

    _print_results(results)
    _exit_for_failed_results(results, fail_on_partial=fail_on_partial)


@ingest_app.command("activities")
def activities(
    user: str = typer.Option(None, "--user", "-u", help="Only ingest for this display name"),
    days_back: int = typer.Option(None, "--days-back", "-d", help="Days to look back (default: from config)"),
    start_date: str = typer.Option(None, "--start-date", help="Explicit start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(None, "--end-date", help="Explicit end date (YYYY-MM-DD)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch data but don't write to DB"),
    include_details: bool = typer.Option(True, "--include-details/--skip-details", help="Fetch chart and polyline details"),
    include_files: bool = typer.Option(True, "--include-files/--skip-files", help="Download original activity files"),
    fail_on_partial: bool = typer.Option(False, "--fail-on-partial", help="Exit non-zero when any selected object is partial"),
) -> None:
    """Ingest activities."""
    if days_back is not None and days_back < 1:
        typer.echo("--days-back must be at least 1", err=True)
        raise typer.Exit(1)

    from garmin_sync.ingest.pipeline import run_for_all_users

    _ensure_db_ready()
    engine = get_engine()
    with Session(engine) as session:
        results = run_for_all_users(
            session,
            start_date=_parse_date_option(start_date, "--start-date"),
            end_date=_parse_date_option(end_date, "--end-date"),
            days_back=days_back,
            user_filter=user,
            dry_run=dry_run,
            data_types=["activities"],
            include_details=include_details,
            include_files=include_files,
        )

    _print_results(results)
    _exit_for_failed_results(results, fail_on_partial=fail_on_partial)


@ingest_app.command("personal-records")
def personal_records(
    user: str = typer.Option(None, "--user", "-u", help="Only ingest for this display name"),
    days_back: int = typer.Option(None, "--days-back", "-d", help="Days to look back (default: from config)"),
    start_date: str = typer.Option(None, "--start-date", help="Explicit start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(None, "--end-date", help="Explicit end date (YYYY-MM-DD)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch data but don't write to DB"),
    fail_on_partial: bool = typer.Option(False, "--fail-on-partial", help="Exit non-zero when any selected object is partial"),
) -> None:
    """Ingest personal records."""
    if days_back is not None and days_back < 1:
        typer.echo("--days-back must be at least 1", err=True)
        raise typer.Exit(1)

    from garmin_sync.ingest.pipeline import run_for_all_users

    _ensure_db_ready()
    engine = get_engine()
    with Session(engine) as session:
        results = run_for_all_users(
            session,
            start_date=_parse_date_option(start_date, "--start-date"),
            end_date=_parse_date_option(end_date, "--end-date"),
            days_back=days_back,
            user_filter=user,
            dry_run=dry_run,
            data_types=["personal_records"],
        )

    _print_results(results)
    _exit_for_failed_results(results, fail_on_partial=fail_on_partial)


if __name__ == "__main__":
    app()
