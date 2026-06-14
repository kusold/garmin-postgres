from datetime import date, timedelta

import typer
from notion_client import Client
from sqlmodel import Session

from garmin_postgres.config import get_settings as get_db_settings
from garmin_postgres.db import get_engine
from notion_sync.config import get_settings
from notion_sync.notion import NotionSink
from notion_sync.sync import DATA_TYPES, run_sync

app = typer.Typer(name="notion-sync", help="Sync archived Garmin data from PostgreSQL to Notion.")


def _date_range(days_back: int | None, start_date: str | None, end_date: str | None) -> tuple[date | None, date | None]:
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
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
    dry_run: bool = typer.Option(False, "--dry-run", help="Query data but don't read or write Notion pages"),
) -> None:
    """Sync archived data to Notion."""
    selected = data_type if data_type else None
    invalid = sorted(set(selected or []) - set(DATA_TYPES))
    if invalid:
        typer.echo(f"Unsupported data type(s): {', '.join(invalid)}", err=True)
        raise typer.Exit(1)

    settings = get_settings()
    if not settings.token and not dry_run:
        typer.echo("NOTION_TOKEN is required unless --dry-run is used", err=True)
        raise typer.Exit(1)

    parsed_start, parsed_end = _date_range(days_back, start_date, end_date)
    client = Client(auth=settings.token or "dry-run")
    sink = NotionSink(client, dry_run=dry_run)

    engine = get_engine()
    with Session(engine) as session:
        results = run_sync(
            session,
            sink,
            settings,
            data_types=selected,
            start_date=parsed_start,
            end_date=parsed_end,
            user_filter=user,
        )

    for dtype, info in results.items():
        typer.echo(f"  {dtype}: {info}")


@app.command()
def config() -> None:
    """Show the database URL and configured Notion targets."""
    notion_settings = get_settings()
    db_settings = get_db_settings()
    typer.echo(f"database_url: {db_settings.database_url}")
    typer.echo(f"notion_timezone: {notion_settings.timezone}")
    typer.echo(f"activities: {'configured' if notion_settings.activities_database_id else 'missing'}")
    typer.echo(f"daily_steps: {'configured' if notion_settings.daily_steps_database_id else 'missing'}")
    typer.echo(f"personal_records: {'configured' if notion_settings.personal_records_database_id else 'missing'}")


if __name__ == "__main__":
    app()
