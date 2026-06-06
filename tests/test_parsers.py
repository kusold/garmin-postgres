import pytest

from garmin_postgres.ingest.parsers.daily_summary import parse_daily_summary


SAMPLE_SUMMARY = {
    "id": 123456789,
    "userProfileId": 987654321,
    "calendarDate": "2026-05-30",
    "totalSteps": 8432,
    "totalDistanceMeters": 6200.0,
    "stepGoal": 10000,
    "totalKilocalories": 2100.5,
    "activeSeconds": 3600,
    "totalStepsGoal": 10000,
    "dailyStepGoal": 10000,
    "restingHeartRate": 52,
    "averageStressLevel": 28,
    "maxStressLevel": 75,
    "bodyBatteryChargedValue": 65,
    "bodyBatteryDrainedValue": 45,
    "moderateIntensityMinutes": 25,
    "vigorousIntensityMinutes": 15,
    "floorsAscended": 5,
    "floorsDescended": 5,
    "floorsAscendedGoal": 10,
}


class TestParseDailySummary:
    def test_parses_valid_response(self):
        result = parse_daily_summary(SAMPLE_SUMMARY, user_id=1)

        assert result.user_id == 1
        assert result.calendar_date.isoformat() == "2026-05-30"
        assert result.raw_json == SAMPLE_SUMMARY
        assert result.id is None  # not yet persisted

    def test_different_date(self):
        raw = {**SAMPLE_SUMMARY, "calendarDate": "2026-01-15"}
        result = parse_daily_summary(raw, user_id=42)

        assert result.calendar_date.isoformat() == "2026-01-15"
        assert result.user_id == 42

    def test_missing_calendar_date_raises_key_error(self):
        raw = {k: v for k, v in SAMPLE_SUMMARY.items() if k != "calendarDate"}

        with pytest.raises(KeyError):
            parse_daily_summary(raw, user_id=1)

    def test_invalid_date_format_raises_value_error(self):
        raw = {**SAMPLE_SUMMARY, "calendarDate": "not-a-date"}

        with pytest.raises(ValueError):
            parse_daily_summary(raw, user_id=1)

    def test_minimal_response(self):
        """Parser only needs calendarDate — other fields are optional."""
        raw = {"calendarDate": "2026-06-01"}
        result = parse_daily_summary(raw, user_id=1)

        assert result.calendar_date.isoformat() == "2026-06-01"
        assert result.raw_json == raw
