from datetime import date, timedelta

import typer
from sqlalchemy import select
from sqlmodel import Session

from garmin_postgres.config import get_settings as get_db_settings
from garmin_postgres.db import get_engine
from garmin_postgres.models.user import User
from notion_sync.run import run_user_sync
from notion_sync.targets import find_user, notion_sync_config

app = typer.Typer(name="notion-sync", help="Sync archived Garmin data from PostgreSQL to Notion.")


def _date_range(days_back: int | None, start_date: str | None, end_date: str | None) -> tuple[date | None, date | None]:
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    if parsed_start and parsed_end and parsed_start > parsed_end:
        typer.echo("--start-date must be on or before --end-date", err=True)
        raise typer.Exit(1)
    if parsed_start and days_back is not None:
        typer.echo("--days-back is ignored when --start-date is provided", err=True)
    if parsed_start:
        return parsed_start, parsed_end
    if days_back is None:
        return None, parsed_end
    if days_back < 1:
        typer.echo("--days-back must be at least 1", err=True)
        raise typer.Exit(1)
    end = parsed_end or date.today()
    return end - timedelta(days=days_back - 1), end


@app.callback()
def main() -> None:
    """Sync archived Garmin data from PostgreSQL to Notion."""


@app.command()
def run(
    user: str = typer.Option(..., "--user", "-u", help="Garmin display name to sync"),
    days_back: int = typer.Option(None, "--days-back", "-d", help="Days to look back"),
    start_date: str = typer.Option(None, "--start-date", help="Explicit start date (YYYY-MM-DD)"),
    end_date: str = typer.Option(None, "--end-date", help="Explicit end date (YYYY-MM-DD)"),
    data_type: list[str] = typer.Option(None, "--data-type", "-t", help="Data types to sync (activities, daily_steps, personal_records)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview archived rows without writing to Notion; reads Notion when a token is configured"),
) -> None:
    """Sync archived data to Notion for one user's configured databases."""
    parsed_start, parsed_end = _date_range(days_back, start_date, end_date)

    engine = get_engine()
    with Session(engine) as session:
        db_user = find_user(session, user)
        if db_user is None:
            typer.echo(f"No Garmin user matches --user {user!r}", err=True)
            raise typer.Exit(1)
    try:
        results = run_user_sync(
            db_user.id,
            data_types=data_type or None,
            start_date=parsed_start,
            end_date=parsed_end,
            dry_run=dry_run,
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error

    for dtype, info in results.items():
        typer.echo(f"  {dtype}: {info}")


@app.command()
def config() -> None:
    """Show the database URL and each user's configured Notion targets."""
    db_settings = get_db_settings()
    typer.echo(f"database_url: {db_settings.database_url}")
    engine = get_engine()
    with Session(engine) as session:
        for db_user in session.scalars(select(User).order_by(User.id)).all():
            token, targets = notion_sync_config(session, db_user.id)
            configured = ", ".join(f"{k}={v}" for k, v in sorted(targets.items())) or "none"
            token_state = "token: yes" if token else "token: no"
            typer.echo(f"{db_user.garmin_display_name}: {configured} ({token_state})")


if __name__ == "__main__":
    app()
