from __future__ import annotations

from datetime import date
from typing import Annotated

import typer

from garmin_sync.ingest.date_windows import parse_date
from garmin_sync.ingest.object_registry import (
    ACTIVITIES,
    DAILY_SUMMARY,
    PERSONAL_RECORDS,
    UnknownIngestObject,
    normalize_data_types,
)

from garmin_orchestrator.deployments import deploy_all
from garmin_orchestrator.flows import garmin_archive_flow


app = typer.Typer(
    name="garmin-orchestrator",
    help="Run and deploy Prefect orchestration for Garmin archival.",
)
run_app = typer.Typer(name="run", help="Run Prefect flows locally.")
app.add_typer(run_app, name="run")


def _parse_date_option(value: str | None, option_name: str) -> date | None:
    try:
        return parse_date(value)
    except ValueError:
        typer.echo(f"{option_name} must be in YYYY-MM-DD format", err=True)
        raise typer.Exit(1)


def _parse_data_type_options(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    try:
        return normalize_data_types(values)
    except UnknownIngestObject as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)


def _run_archive(
    *,
    user: str | None,
    data_types: list[str] | None,
    days_back: int | None,
    start_date: str | None,
    end_date: str | None,
    dry_run: bool,
    fail_on_partial: bool,
    include_details: bool,
    include_files: bool,
) -> None:
    if days_back is not None and days_back < 1:
        typer.echo("--days-back must be at least 1", err=True)
        raise typer.Exit(1)

    garmin_archive_flow(
        user=user,
        data_types=data_types,
        days_back=days_back,
        start_date=_parse_date_option(start_date, "--start-date"),
        end_date=_parse_date_option(end_date, "--end-date"),
        dry_run=dry_run,
        fail_on_partial=fail_on_partial,
        include_details=include_details,
        include_files=include_files,
    )


@app.callback()
def main() -> None:
    """Run and deploy Prefect orchestration for Garmin archival."""


@app.command()
def deploy() -> None:
    """Deploy all versioned Prefect deployments from prefect.yaml."""
    deploy_all()


@run_app.command("archive")
def run_archive(
    user: Annotated[
        str | None,
        typer.Option("--user", "-u", help="Only ingest for this display name"),
    ] = None,
    days_back: Annotated[
        int | None,
        typer.Option("--days-back", "-d", help="Days to look back"),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Explicit start date (YYYY-MM-DD)"),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Explicit end date (YYYY-MM-DD)"),
    ] = None,
    data_type: Annotated[
        list[str] | None,
        typer.Option(
            "--data-type",
            "-t",
            help="Data types to ingest (daily-summary, activities, personal-records)",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Fetch data but don't write to DB"),
    ] = False,
    fail_on_partial: Annotated[
        bool,
        typer.Option(
            "--fail-on-partial",
            help="Exit non-zero when any selected object is partial",
        ),
    ] = False,
    include_details: Annotated[
        bool,
        typer.Option("--include-details/--skip-details", help="Fetch activity details"),
    ] = True,
    include_files: Annotated[
        bool,
        typer.Option("--include-files/--skip-files", help="Download activity files"),
    ] = True,
) -> None:
    """Run the full Garmin archive Prefect flow locally."""
    _run_archive(
        user=user,
        data_types=_parse_data_type_options(data_type),
        days_back=days_back,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        fail_on_partial=fail_on_partial,
        include_details=include_details,
        include_files=include_files,
    )


@run_app.command("daily-summary")
def run_daily_summary(
    user: Annotated[
        str | None,
        typer.Option("--user", "-u", help="Only ingest for this display name"),
    ] = None,
    days_back: Annotated[
        int | None,
        typer.Option("--days-back", "-d", help="Days to look back"),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Explicit start date (YYYY-MM-DD)"),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Explicit end date (YYYY-MM-DD)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Fetch data but don't write to DB"),
    ] = False,
    fail_on_partial: Annotated[
        bool,
        typer.Option(
            "--fail-on-partial",
            help="Exit non-zero when any selected object is partial",
        ),
    ] = False,
) -> None:
    """Run only daily-summary orchestration locally."""
    _run_archive(
        user=user,
        data_types=[DAILY_SUMMARY],
        days_back=days_back,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        fail_on_partial=fail_on_partial,
        include_details=True,
        include_files=True,
    )


@run_app.command("activities")
def run_activities(
    user: Annotated[
        str | None,
        typer.Option("--user", "-u", help="Only ingest for this display name"),
    ] = None,
    days_back: Annotated[
        int | None,
        typer.Option("--days-back", "-d", help="Days to look back"),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Explicit start date (YYYY-MM-DD)"),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Explicit end date (YYYY-MM-DD)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Fetch data but don't write to DB"),
    ] = False,
    include_details: Annotated[
        bool,
        typer.Option("--include-details/--skip-details", help="Fetch activity details"),
    ] = True,
    include_files: Annotated[
        bool,
        typer.Option("--include-files/--skip-files", help="Download activity files"),
    ] = True,
    fail_on_partial: Annotated[
        bool,
        typer.Option(
            "--fail-on-partial",
            help="Exit non-zero when any selected object is partial",
        ),
    ] = False,
) -> None:
    """Run only activities orchestration locally."""
    _run_archive(
        user=user,
        data_types=[ACTIVITIES],
        days_back=days_back,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        fail_on_partial=fail_on_partial,
        include_details=include_details,
        include_files=include_files,
    )


@run_app.command("personal-records")
def run_personal_records(
    user: Annotated[
        str | None,
        typer.Option("--user", "-u", help="Only ingest for this display name"),
    ] = None,
    days_back: Annotated[
        int | None,
        typer.Option("--days-back", "-d", help="Days to look back"),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Explicit start date (YYYY-MM-DD)"),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Explicit end date (YYYY-MM-DD)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Fetch data but don't write to DB"),
    ] = False,
    fail_on_partial: Annotated[
        bool,
        typer.Option(
            "--fail-on-partial",
            help="Exit non-zero when any selected object is partial",
        ),
    ] = False,
) -> None:
    """Run only personal-records orchestration locally."""
    _run_archive(
        user=user,
        data_types=[PERSONAL_RECORDS],
        days_back=days_back,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        fail_on_partial=fail_on_partial,
        include_details=True,
        include_files=True,
    )


if __name__ == "__main__":
    app()
