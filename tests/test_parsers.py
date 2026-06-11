from datetime import datetime, timezone

import pytest

from garmin_postgres.ingest.parsers.activity import parse_activity
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


SAMPLE_ACTIVITY = {
    "activityId": 16204035614,
    "activityName": "Morning Run",
    "activityType": {"typeKey": "running", "typeId": 1, "parentTypeId": 17},
    "eventType": {"typeId": 9, "typeKey": "uncategorized"},
    "startTimeLocal": "2026-06-01 07:30:00",
    "startTimeGMT": "2026-06-01 12:30:00",
    "distance": 5000.0,
    "duration": 1890.0,
    "elapsedDuration": 1900.0,
    "movingDuration": 1880.0,
    "averageSpeed": 2.646,
    "maxSpeed": 5.0,
    "calories": 350.0,
    "averageHR": 145.0,
    "maxHR": 172.0,
    "steps": 5234,
    "elevationGain": 50.0,
    "elevationLoss": 50.0,
}


class TestParseActivity:
    def test_parses_valid_response(self):
        result = parse_activity(SAMPLE_ACTIVITY, user_id=1)

        assert result.user_id == 1
        assert result.activity_id == 16204035614
        assert result.activity_type == "running"
        assert result.start_time is not None
        assert result.start_time.year == 2026
        assert result.start_time.month == 6
        assert result.start_time.day == 1
        assert result.raw_json == SAMPLE_ACTIVITY
        assert result.id is None  # not yet persisted

    def test_extracts_activity_type(self):
        result = parse_activity(SAMPLE_ACTIVITY, user_id=1)

        assert result.activity_type == "running"

    def test_parses_start_time(self):
        result = parse_activity(SAMPLE_ACTIVITY, user_id=42)

        assert result.start_time is not None
        assert result.start_time == datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
        assert result.start_time.hour == 12
        assert result.start_time.minute == 30
        assert result.start_time.tzinfo == timezone.utc

    def test_missing_activity_id_raises_key_error(self):
        raw = {k: v for k, v in SAMPLE_ACTIVITY.items() if k != "activityId"}

        with pytest.raises(KeyError):
            parse_activity(raw, user_id=1)

    def test_missing_activity_type_is_none(self):
        raw = {k: v for k, v in SAMPLE_ACTIVITY.items() if k != "activityType"}
        result = parse_activity(raw, user_id=1)

        assert result.activity_type is None
        assert result.activity_id == 16204035614

    def test_missing_utc_start_time_is_none(self):
        raw = {k: v for k, v in SAMPLE_ACTIVITY.items() if k != "startTimeGMT"}
        result = parse_activity(raw, user_id=1)

        assert result.start_time is None

    def test_minimal_response_only_activity_id(self):
        """Parser only needs activityId — other fields are optional."""
        raw = {"activityId": 999}
        result = parse_activity(raw, user_id=1)

        assert result.activity_id == 999
        assert result.activity_type is None
        assert result.start_time is None
        assert result.raw_json == raw
