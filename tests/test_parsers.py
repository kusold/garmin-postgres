from datetime import date, datetime, timezone

import pytest

from garmin_sync.ingest.parsers.activity import parse_activity
from garmin_sync.ingest.parsers.daily_summary import parse_daily_summary
from garmin_sync.ingest.parsers.personal_record import (
    parse_personal_record,
    parse_personal_records,
)


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

    def test_parses_activity_detail_response_shape(self):
        raw = {
            "activityId": 23444533321,
            "summaryDTO": {"startTimeGMT": "2026-07-01T16:32:31.0"},
            "activityTypeDTO": {"typeKey": "strength_training", "typeId": 13},
        }

        result = parse_activity(raw, user_id=42)

        assert result.activity_type == "strength_training"
        assert result.start_time == datetime(2026, 7, 1, 16, 32, 31, tzinfo=timezone.utc)

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


SAMPLE_PERSONAL_RECORD = {
    "typeId": 3,
    "value": "00:22:14",
    "prStartTimeGmtFormatted": "2026-06-01 12:30:00",
    "activityType": {"typeKey": "running", "typeId": 1},
    "activityId": 16204035614,
}


class TestParsePersonalRecord:
    def test_parses_valid_response(self):
        result = parse_personal_record(SAMPLE_PERSONAL_RECORD, user_id=1)

        assert result.user_id == 1
        assert result.type_id == 3
        assert result.record_date == date(2026, 6, 1)
        assert result.activity_type == "running"
        assert result.value_text == "00:22:14"
        assert result.raw_json == SAMPLE_PERSONAL_RECORD
        assert result.id is None

    def test_parses_iso_timestamp_date_component(self):
        raw = {
            **SAMPLE_PERSONAL_RECORD,
            "prStartTimeGmtFormatted": "2026-06-02T12:30:00.000Z",
        }

        result = parse_personal_record(raw, user_id=1)

        assert result.record_date == date(2026, 6, 2)

    def test_missing_record_date_raises_key_error(self):
        raw = {"typeId": 16}

        with pytest.raises(KeyError):
            parse_personal_record(raw, user_id=1)

    def test_missing_value_raises_key_error(self):
        raw = {k: v for k, v in SAMPLE_PERSONAL_RECORD.items() if k != "value"}

        with pytest.raises(KeyError):
            parse_personal_record(raw, user_id=1)

    def test_empty_value_raises_value_error(self):
        raw = {**SAMPLE_PERSONAL_RECORD, "value": None}

        with pytest.raises(ValueError):
            parse_personal_record(raw, user_id=1)

    def test_missing_type_id_raises_key_error(self):
        raw = {k: v for k, v in SAMPLE_PERSONAL_RECORD.items() if k != "typeId"}

        with pytest.raises(KeyError):
            parse_personal_record(raw, user_id=1)

    def test_activity_type_string_is_preserved(self):
        raw = {**SAMPLE_PERSONAL_RECORD, "activityType": "cycling"}

        result = parse_personal_record(raw, user_id=1)

        assert result.activity_type == "cycling"

    def test_parse_personal_records_list(self):
        raw_records = [
            SAMPLE_PERSONAL_RECORD,
            {**SAMPLE_PERSONAL_RECORD, "typeId": 4, "value": "00:47:30"},
        ]

        results = parse_personal_records(raw_records, user_id=42)

        assert [result.type_id for result in results] == [3, 4]
        assert {result.user_id for result in results} == {42}
