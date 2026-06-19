from datetime import date

import pytest
from typer.testing import CliRunner

from garmin_sync.cli import app
from garmin_sync.ingest.date_windows import iter_dates, resolve_date_window
from garmin_sync.ingest.object_registry import (
    DEFAULT_DATA_TYPES,
    UnknownIngestObject,
    normalize_data_type,
    normalize_data_types,
)
from garmin_sync.ingest.results import IngestResult, aggregate_results


class TestObjectRegistry:
    def test_normalizes_supported_aliases(self):
        assert normalize_data_type("daily-summary") == "daily_summary"
        assert normalize_data_type("daily_summary") == "daily_summary"
        assert normalize_data_type("daily") == "daily_summary"
        assert normalize_data_type("activities") == "activities"
        assert normalize_data_type("personal-records") == "personal_records"
        assert normalize_data_type("personal_records") == "personal_records"

    def test_default_data_types_are_ordered_for_umbrella_ingestion(self):
        assert normalize_data_types(None) == DEFAULT_DATA_TYPES

    def test_duplicate_aliases_collapse_to_one_canonical_type(self):
        assert normalize_data_types(["daily-summary", "daily_summary"]) == [
            "daily_summary"
        ]

    def test_unknown_data_type_raises_clear_error(self):
        with pytest.raises(UnknownIngestObject, match="Unknown ingest data type"):
            normalize_data_type("sleep")


class TestDateWindows:
    def test_defaults_to_yesterday_and_configured_lookback(self):
        window = resolve_date_window(
            default_days_back=3,
            today=date(2026, 6, 12),
        )

        assert window.start_date == date(2026, 6, 9)
        assert window.end_date == date(2026, 6, 11)

    def test_explicit_start_and_end_are_inclusive(self):
        assert list(iter_dates(date(2026, 6, 1), date(2026, 6, 3))) == [
            date(2026, 6, 1),
            date(2026, 6, 2),
            date(2026, 6, 3),
        ]

    def test_rejects_invalid_window(self):
        with pytest.raises(ValueError, match="start_date"):
            resolve_date_window(
                start_date=date(2026, 6, 3),
                end_date=date(2026, 6, 1),
                default_days_back=1,
            )


class TestIngestResults:
    def test_partial_children_make_aggregate_partial_without_losing_metrics(self):
        result = aggregate_results(
            "activities",
            [
                IngestResult.success(
                    "activities",
                    rows=1,
                    metrics={"detail_rows": 1, "detail_errors": 0},
                ),
                IngestResult(
                    data_type="activities",
                    status="partial",
                    rows=1,
                    errors=0,
                    metrics={"detail_rows": 0, "detail_errors": 1},
                ),
            ],
        )

        assert result.as_dict() == {
            "status": "partial",
            "rows": 2,
            "errors": 0,
            "detail_errors": 1,
            "detail_rows": 1,
        }


class TestIngestCliValidation:
    def test_invalid_data_type_fails_before_database_setup(self, monkeypatch):
        def fail_db_ready():
            raise AssertionError("database should not be opened")

        monkeypatch.setattr("garmin_sync.cli._ensure_db_ready", fail_db_ready)

        result = CliRunner().invoke(
            app,
            ["ingest", "run", "--data-type", "sleep"],
        )

        assert result.exit_code == 1
        assert "Unknown ingest data type 'sleep'" in result.output
