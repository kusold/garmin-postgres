"""Behavior shared by direct and Prefect activity archive runs."""

from garmin_sync.ingest.activity_run import ActivityArchiveRun, activity_step_result
from garmin_sync.ingest.results import IngestResult


def test_detail_failure_keeps_file_work_and_reports_one_partial_activity():
    steps = []

    def execute(step):
        steps.append(step)
        return activity_step_result(
            step,
            succeeded=step == "file",
            error="details unavailable" if step == "detail" else None,
        )

    result = ActivityArchiveRun().complete(
        IngestResult.success("activities", rows=1),
        execute,
    )

    assert steps == ["detail", "file"]
    assert result.as_dict() == {
        "status": "partial",
        "rows": 1,
        "errors": 1,
        "detail_rows": 0,
        "detail_errors": 1,
        "file_rows": 1,
        "file_errors": 0,
        "error": "details unavailable",
    }


def test_dry_run_keeps_detail_but_skips_file():
    steps = []

    def execute(step):
        steps.append(step)
        return activity_step_result(step, succeeded=True)

    result = ActivityArchiveRun(dry_run=True).complete(
        IngestResult.success("activities", rows=1),
        execute,
    )

    assert steps == ["detail"]
    assert result.as_dict() == {
        "status": "success",
        "rows": 1,
        "errors": 0,
        "detail_rows": 1,
        "detail_errors": 0,
    }


def test_failed_summary_does_not_start_optional_work():
    summary = IngestResult.error_result("activities", error="summary unavailable")

    def execute(_step):
        raise AssertionError("optional work must not run after summary failure")

    assert ActivityArchiveRun().complete(summary, execute) is summary
