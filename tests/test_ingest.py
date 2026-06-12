from datetime import date, datetime, timezone

from sqlalchemy import select

import garmin_postgres.ingest.pipeline as pipeline
from garmin_postgres.ingest.pipeline import (
    upsert_activity,
    upsert_activity_detail,
    upsert_activity_file,
    upsert_daily_summary,
    upsert_personal_record,
)
from garmin_postgres.models.activity import Activity
from garmin_postgres.models.activity_detail import ActivityDetail
from garmin_postgres.models.activity_file import ActivityFile
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord
from garmin_postgres.models.user import User


def _create_user(session) -> User:
    user = User(
        garmin_display_name="testuser",
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


class TestUpsertDailySummary:
    def test_insert_new_summary(self, session):
        user = _create_user(session)
        summary = DailySummary(
            user_id=user.id,
            calendar_date=date(2026, 6, 1),
            raw_json={"calendarDate": "2026-06-01", "totalSteps": 5000},
        )

        upsert_daily_summary(session, summary)

        result = session.scalars(
            select(DailySummary).where(
                DailySummary.user_id == user.id,
                DailySummary.calendar_date == date(2026, 6, 1),
            )
        ).first()
        assert result is not None
        assert result.raw_json["totalSteps"] == 5000

    def test_upsert_updates_existing(self, session):
        user = _create_user(session)

        # Insert first version
        summary_v1 = DailySummary(
            user_id=user.id,
            calendar_date=date(2026, 6, 1),
            raw_json={"calendarDate": "2026-06-01", "totalSteps": 5000},
        )
        upsert_daily_summary(session, summary_v1)

        # Upsert with updated data
        summary_v2 = DailySummary(
            user_id=user.id,
            calendar_date=date(2026, 6, 1),
            raw_json={"calendarDate": "2026-06-01", "totalSteps": 8432},
        )
        upsert_daily_summary(session, summary_v2)

        results = session.scalars(
            select(DailySummary).where(
                DailySummary.user_id == user.id,
                DailySummary.calendar_date == date(2026, 6, 1),
            )
        ).all()
        assert len(results) == 1
        assert results[0].raw_json["totalSteps"] == 8432

    def test_different_dates_are_separate_rows(self, session):
        user = _create_user(session)

        for day in range(3):
            d = date(2026, 6, 1 + day)
            summary = DailySummary(
                user_id=user.id,
                calendar_date=d,
                raw_json={"calendarDate": d.isoformat(), "totalSteps": 1000 * (day + 1)},
            )
            upsert_daily_summary(session, summary)

        results = session.scalars(
            select(DailySummary).where(DailySummary.user_id == user.id)
        ).all()
        assert len(results) == 3

    def test_different_users_same_date(self, session):
        user1 = _create_user(session)
        user2 = User(garmin_display_name="testuser2", is_active=True)
        session.add(user2)
        session.flush()

        for user in [user1, user2]:
            summary = DailySummary(
                user_id=user.id,
                calendar_date=date(2026, 6, 1),
                raw_json={"calendarDate": "2026-06-01", "totalSteps": user.id * 1000},
            )
            upsert_daily_summary(session, summary)

        results = session.scalars(
            select(DailySummary).where(DailySummary.calendar_date == date(2026, 6, 1))
        ).all()
        assert len(results) == 2


def _create_activity(session, user_id, activity_id=100) -> Activity:
    """Helper to create and upsert an activity."""
    activity = Activity(
        user_id=user_id,
        activity_id=activity_id,
        activity_type="running",
        start_time=datetime(2026, 6, 1, 7, 30, tzinfo=timezone.utc),
        raw_json={"activityId": activity_id, "activityName": "Test Run"},
    )
    upsert_activity(session, activity)
    return activity


class TestUpsertActivity:
    def test_insert_new_activity(self, session):
        user = _create_user(session)
        activity = Activity(
            user_id=user.id,
            activity_id=1001,
            activity_type="running",
            start_time=datetime(2026, 6, 1, 7, 30, tzinfo=timezone.utc),
            raw_json={"activityId": 1001, "distance": 5000},
        )

        upsert_activity(session, activity)

        result = session.scalars(
            select(Activity).where(
                Activity.user_id == user.id,
                Activity.activity_id == 1001,
            )
        ).first()
        assert result is not None
        assert result.activity_type == "running"
        assert result.raw_json["distance"] == 5000

    def test_upsert_updates_existing(self, session):
        user = _create_user(session)

        activity_v1 = Activity(
            user_id=user.id,
            activity_id=2001,
            activity_type="running",
            start_time=datetime(2026, 6, 1, 7, 30, tzinfo=timezone.utc),
            raw_json={"activityId": 2001, "distance": 5000},
        )
        upsert_activity(session, activity_v1)

        activity_v2 = Activity(
            user_id=user.id,
            activity_id=2001,
            activity_type="running",
            start_time=datetime(2026, 6, 1, 7, 30, tzinfo=timezone.utc),
            raw_json={"activityId": 2001, "distance": 10000},
        )
        upsert_activity(session, activity_v2)

        results = session.scalars(
            select(Activity).where(
                Activity.user_id == user.id,
                Activity.activity_id == 2001,
            )
        ).all()
        assert len(results) == 1
        assert results[0].raw_json["distance"] == 10000

    def test_different_activities_are_separate_rows(self, session):
        user = _create_user(session)

        for i in range(3):
            activity = Activity(
                user_id=user.id,
                activity_id=3000 + i,
                activity_type="running",
                start_time=datetime(2026, 6, 1 + i, 7, 30, tzinfo=timezone.utc),
                raw_json={"activityId": 3000 + i},
            )
            upsert_activity(session, activity)

        results = session.scalars(
            select(Activity).where(Activity.user_id == user.id)
        ).all()
        assert len(results) == 3

    def test_different_users_same_activity_id(self, session):
        user1 = _create_user(session)
        user2 = User(garmin_display_name="testuser2", is_active=True)
        session.add(user2)
        session.flush()

        for user in [user1, user2]:
            activity = Activity(
                user_id=user.id,
                activity_id=4001,
                activity_type="cycling",
                start_time=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
                raw_json={"activityId": 4001, "user": user.garmin_display_name},
            )
            upsert_activity(session, activity)

        results = session.scalars(
            select(Activity).where(Activity.activity_id == 4001)
        ).all()
        assert len(results) == 2

    def test_upsert_returns_db_id(self, session):
        user = _create_user(session)
        activity = _create_activity(session, user.id, activity_id=5001)

        assert activity.id is not None


class TestUpsertActivityFile:
    def test_insert_new_file(self, session):
        user = _create_user(session)
        activity = _create_activity(session, user.id, activity_id=6001)

        af = ActivityFile(
            activity_id=activity.id,
            file_format="fit",
            file_data=b"fake fit data",
            raw_json={"source_format": "original"},
        )
        upsert_activity_file(session, af)

        result = session.scalars(
            select(ActivityFile).where(
                ActivityFile.activity_id == activity.id,
                ActivityFile.file_format == "fit",
            )
        ).first()
        assert result is not None
        assert result.file_data == b"fake fit data"

    def test_upsert_updates_file_data(self, session):
        user = _create_user(session)
        activity = _create_activity(session, user.id, activity_id=7001)

        af_v1 = ActivityFile(
            activity_id=activity.id,
            file_format="fit",
            file_data=b"version 1",
            raw_json={"version": 1},
        )
        upsert_activity_file(session, af_v1)

        af_v2 = ActivityFile(
            activity_id=activity.id,
            file_format="fit",
            file_data=b"version 2",
            raw_json={"version": 2},
        )
        upsert_activity_file(session, af_v2)

        results = session.scalars(
            select(ActivityFile).where(
                ActivityFile.activity_id == activity.id,
                ActivityFile.file_format == "fit",
            )
        ).all()
        assert len(results) == 1
        assert results[0].file_data == b"version 2"
        assert results[0].raw_json["version"] == 2

    def test_different_formats_are_separate_rows(self, session):
        user = _create_user(session)
        activity = _create_activity(session, user.id, activity_id=8001)

        for fmt in ["fit", "gpx", "tcx"]:
            af = ActivityFile(
                activity_id=activity.id,
                file_format=fmt,
                file_data=f"fake {fmt} data".encode(),
            )
            upsert_activity_file(session, af)

        results = session.scalars(
            select(ActivityFile).where(ActivityFile.activity_id == activity.id)
        ).all()
        assert len(results) == 3
        assert {r.file_format for r in results} == {"fit", "gpx", "tcx"}


class TestUpsertActivityDetail:
    def test_insert_new_detail(self, session):
        user = _create_user(session)
        activity = _create_activity(session, user.id, activity_id=9001)

        detail = ActivityDetail(
            activity_id=activity.id,
            max_chart_size=2000,
            max_polyline_size=4000,
            raw_json={"activityId": 9001, "activityDetailMetrics": [{"metric": "hr"}]},
        )
        upsert_activity_detail(session, detail)

        result = session.scalars(
            select(ActivityDetail).where(ActivityDetail.activity_id == activity.id)
        ).first()
        assert result is not None
        assert result.max_chart_size == 2000
        assert result.max_polyline_size == 4000
        assert result.raw_json["activityDetailMetrics"][0]["metric"] == "hr"

    def test_upsert_updates_existing_detail(self, session):
        user = _create_user(session)
        activity = _create_activity(session, user.id, activity_id=9002)

        detail_v1 = ActivityDetail(
            activity_id=activity.id,
            max_chart_size=2000,
            max_polyline_size=4000,
            raw_json={"version": 1},
        )
        upsert_activity_detail(session, detail_v1)

        detail_v2 = ActivityDetail(
            activity_id=activity.id,
            max_chart_size=1000,
            max_polyline_size=3000,
            raw_json={"version": 2},
        )
        upsert_activity_detail(session, detail_v2)

        results = session.scalars(
            select(ActivityDetail).where(ActivityDetail.activity_id == activity.id)
        ).all()
        assert len(results) == 1
        assert results[0].max_chart_size == 1000
        assert results[0].max_polyline_size == 3000
        assert results[0].raw_json["version"] == 2

    def test_different_activities_get_separate_details(self, session):
        user = _create_user(session)
        activity1 = _create_activity(session, user.id, activity_id=9003)
        activity2 = _create_activity(session, user.id, activity_id=9004)

        for activity in [activity1, activity2]:
            detail = ActivityDetail(
                activity_id=activity.id,
                max_chart_size=2000,
                max_polyline_size=4000,
                raw_json={"activityId": activity.activity_id},
            )
            upsert_activity_detail(session, detail)

        results = session.scalars(select(ActivityDetail)).all()
        assert len(results) == 2
        assert {r.raw_json["activityId"] for r in results} == {9003, 9004}


class TestRunIngestionActivityDetails:
    def test_fetches_and_stores_activity_details_after_activity_upsert(self, session, monkeypatch):
        user = _create_user(session)
        calls = []

        class FakeGarminClient:
            def __init__(self, garmin):
                self.garmin = garmin

            def get_activities_by_date(self, startdate, enddate):
                return [{"activityId": 10001}]

            def get_activity(self, activity_id):
                calls.append(("summary", activity_id))
                return {
                    "activityId": int(activity_id),
                    "activityType": {"typeKey": "running"},
                    "startTimeGMT": "2026-06-01 12:30:00",
                }

            def get_activity_details(self, activity_id, maxchart=2000, maxpoly=4000):
                stored = session.scalars(
                    select(Activity).where(Activity.activity_id == int(activity_id))
                ).first()
                assert stored is not None
                assert stored.id is not None
                calls.append(("details", activity_id, maxchart, maxpoly))
                return {"activityId": int(activity_id), "geoPolyline": {"points": []}}

            def download_activity(self, activity_id):
                calls.append(("download", activity_id))
                return b"fit data"

        monkeypatch.setattr(pipeline, "load_user_client", lambda session, user: object())
        monkeypatch.setattr(pipeline, "GarminClient", FakeGarminClient)
        monkeypatch.setattr(pipeline, "save_tokens", lambda session, user, garmin: None)
        monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)

        result = pipeline.run_ingestion(
            session,
            user,
            date(2026, 6, 1),
            date(2026, 6, 1),
            data_types=["activities"],
        )

        detail = session.scalars(select(ActivityDetail)).one()
        assert detail.raw_json["geoPolyline"]["points"] == []
        assert detail.max_chart_size == 2000
        assert detail.max_polyline_size == 4000
        assert result["activities"] == {
            "status": "success",
            "rows": 1,
            "errors": 0,
            "detail_rows": 1,
            "detail_errors": 0,
        }
        assert calls == [
            ("summary", "10001"),
            ("details", "10001", 2000, 4000),
            ("download", "10001"),
        ]

    def test_detail_failure_does_not_prevent_activity_storage(self, session, monkeypatch):
        user = _create_user(session)

        class FakeGarminClient:
            def __init__(self, garmin):
                self.garmin = garmin

            def get_activities_by_date(self, startdate, enddate):
                return [{"activityId": 10002}]

            def get_activity(self, activity_id):
                return {
                    "activityId": int(activity_id),
                    "activityType": {"typeKey": "cycling"},
                    "startTimeGMT": "2026-06-01 13:30:00",
                }

            def get_activity_details(self, activity_id, maxchart=2000, maxpoly=4000):
                raise RuntimeError("details unavailable")

            def download_activity(self, activity_id):
                return b"fit data"

        monkeypatch.setattr(pipeline, "load_user_client", lambda session, user: object())
        monkeypatch.setattr(pipeline, "GarminClient", FakeGarminClient)
        monkeypatch.setattr(pipeline, "save_tokens", lambda session, user, garmin: None)
        monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)

        result = pipeline.run_ingestion(
            session,
            user,
            date(2026, 6, 1),
            date(2026, 6, 1),
            data_types=["activities"],
        )

        activity = session.scalars(
            select(Activity).where(Activity.activity_id == 10002)
        ).first()
        details = session.scalars(select(ActivityDetail)).all()
        assert activity is not None
        assert details == []
        assert result["activities"] == {
            "status": "success",
            "rows": 1,
            "errors": 0,
            "detail_rows": 0,
            "detail_errors": 1,
        }

    def test_dry_run_fetches_details_without_writing_rows(self, session, monkeypatch):
        user = _create_user(session)
        calls = []

        class FakeGarminClient:
            def __init__(self, garmin):
                self.garmin = garmin

            def get_activities_by_date(self, startdate, enddate):
                return [{"activityId": 10003}]

            def get_activity(self, activity_id):
                return {"activityId": int(activity_id)}

            def get_activity_details(self, activity_id, maxchart=2000, maxpoly=4000):
                calls.append(("details", activity_id, maxchart, maxpoly))
                return {"activityId": int(activity_id), "activityDetailMetrics": []}

            def download_activity(self, activity_id):
                raise AssertionError("dry run should not download activity files")

        monkeypatch.setattr(pipeline, "load_user_client", lambda session, user: object())
        monkeypatch.setattr(pipeline, "GarminClient", FakeGarminClient)
        monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)

        result = pipeline.run_ingestion(
            session,
            user,
            date(2026, 6, 1),
            date(2026, 6, 1),
            dry_run=True,
            data_types=["activities"],
        )

        assert session.scalars(select(Activity)).all() == []
        assert session.scalars(select(ActivityDetail)).all() == []
        assert calls == [("details", "10003", 2000, 4000)]
        assert result["activities"] == {
            "status": "success",
            "rows": 1,
            "errors": 0,
            "detail_rows": 1,
            "detail_errors": 0,
        }


class TestUpsertPersonalRecord:
    def test_insert_new_record(self, session):
        user = _create_user(session)
        record = PersonalRecord(
            user_id=user.id,
            type_id=3,
            record_date=date(2026, 6, 1),
            activity_type="running",
            value_text="00:22:14",
            raw_json={"typeId": 3, "value": "00:22:14"},
        )

        upsert_personal_record(session, record)

        result = session.scalars(
            select(PersonalRecord).where(
                PersonalRecord.user_id == user.id,
                PersonalRecord.type_id == 3,
            )
        ).first()
        assert result is not None
        assert result.activity_type == "running"
        assert result.raw_json["value"] == "00:22:14"

    def test_upsert_updates_existing(self, session):
        user = _create_user(session)
        record_v1 = PersonalRecord(
            user_id=user.id,
            type_id=3,
            record_date=date(2026, 6, 1),
            activity_type="running",
            value_text="00:22:14",
            raw_json={"typeId": 3, "value": "00:22:14", "version": 1},
        )
        upsert_personal_record(session, record_v1)

        record_v2 = PersonalRecord(
            user_id=user.id,
            type_id=3,
            record_date=date(2026, 6, 1),
            activity_type="trail_running",
            value_text="00:22:14",
            raw_json={"typeId": 3, "value": "00:22:14", "version": 2},
        )
        upsert_personal_record(session, record_v2)

        results = session.scalars(
            select(PersonalRecord).where(
                PersonalRecord.user_id == user.id,
                PersonalRecord.type_id == 3,
            )
        ).all()
        assert len(results) == 1
        assert results[0].activity_type == "trail_running"
        assert results[0].raw_json["version"] == 2

    def test_different_users_same_record_identity(self, session):
        user1 = _create_user(session)
        user2 = User(garmin_display_name="testuser2", is_active=True)
        session.add(user2)
        session.flush()

        for user in [user1, user2]:
            record = PersonalRecord(
                user_id=user.id,
                type_id=3,
                record_date=date(2026, 6, 1),
                activity_type="running",
                value_text="00:22:14",
                raw_json={"typeId": 3, "user": user.garmin_display_name},
            )
            upsert_personal_record(session, record)

        results = session.scalars(
            select(PersonalRecord).where(PersonalRecord.type_id == 3)
        ).all()
        assert len(results) == 2

    def test_preserves_history_for_same_type_when_date_or_value_differs(self, session):
        user = _create_user(session)
        records = [
            PersonalRecord(
                user_id=user.id,
                type_id=3,
                record_date=date(2026, 6, 1),
                activity_type="running",
                value_text="00:22:14",
                raw_json={"typeId": 3, "value": "00:22:14"},
            ),
            PersonalRecord(
                user_id=user.id,
                type_id=3,
                record_date=date(2026, 7, 1),
                activity_type="running",
                value_text="00:21:45",
                raw_json={"typeId": 3, "value": "00:21:45"},
            ),
        ]

        for record in records:
            upsert_personal_record(session, record)

        results = session.scalars(
            select(PersonalRecord).where(
                PersonalRecord.user_id == user.id,
                PersonalRecord.type_id == 3,
            )
        ).all()
        assert len(results) == 2


class TestRunForAllUsersDateRange:
    def test_days_back_one_fetches_one_day_ending_yesterday(self, monkeypatch):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 6, 12)

        user = User(garmin_display_name="testuser", is_active=True)
        calls = []

        def fake_run_ingestion(
            session, user, start_date, end_date, *, dry_run=False, data_types=None
        ):
            calls.append((start_date, end_date))
            return {"daily_summary": {"status": "success", "rows": 0, "errors": 0}}

        monkeypatch.setattr(pipeline, "date", FixedDate)
        monkeypatch.setattr(
            pipeline, "get_active_users", lambda session, user_filter=None: [user]
        )
        monkeypatch.setattr(pipeline, "run_ingestion", fake_run_ingestion)

        pipeline.run_for_all_users(object(), days_back=1)

        assert calls == [(date(2026, 6, 11), date(2026, 6, 11))]

    def test_days_back_fetches_exact_inclusive_day_count_with_explicit_end_date(self, monkeypatch):
        user = User(garmin_display_name="testuser", is_active=True)
        calls = []

        def fake_run_ingestion(
            session, user, start_date, end_date, *, dry_run=False, data_types=None
        ):
            calls.append((start_date, end_date))
            return {"daily_summary": {"status": "success", "rows": 0, "errors": 0}}

        monkeypatch.setattr(
            pipeline, "get_active_users", lambda session, user_filter=None: [user]
        )
        monkeypatch.setattr(pipeline, "run_ingestion", fake_run_ingestion)

        pipeline.run_for_all_users(
            object(),
            end_date=date(2026, 6, 11),
            days_back=7,
        )

        start_date, end_date = calls[0]
        assert (end_date - start_date).days + 1 == 7
        assert calls == [(date(2026, 6, 5), date(2026, 6, 11))]


class FakeGarminClient:
    def __init__(self, *, personal_records=None, personal_error=None):
        self.garmin = object()
        self.personal_records = personal_records or []
        self.personal_error = personal_error
        self.calls = []

    def get_daily_summary(self, cdate):
        self.calls.append(("daily_summary", cdate))
        return {"calendarDate": cdate, "totalSteps": 1234}

    def get_activities_by_date(self, startdate, enddate):
        self.calls.append(("activities", startdate, enddate))
        return []

    def get_personal_records(self):
        self.calls.append(("personal_records",))
        if self.personal_error:
            raise self.personal_error
        return self.personal_records


class TestRunIngestionPersonalRecords:
    def test_default_ingestion_includes_personal_records(self, session, monkeypatch):
        user = _create_user(session)
        fake_client = FakeGarminClient(
            personal_records=[
                {
                    "typeId": 3,
                    "value": "00:22:14",
                    "prStartTimeGmtFormatted": "2026-06-01 12:30:00",
                }
            ]
        )
        monkeypatch.setattr(pipeline, "load_user_client", lambda session, user: object())
        monkeypatch.setattr(pipeline, "GarminClient", lambda garmin: fake_client)
        monkeypatch.setattr(pipeline, "save_tokens", lambda session, user, garmin: None)

        result = pipeline.run_ingestion(
            session,
            user,
            date(2026, 6, 1),
            date(2026, 6, 1),
        )

        assert result["personal_records"] == {
            "status": "success",
            "rows": 1,
            "errors": 0,
        }
        assert ("personal_records",) in fake_client.calls

    def test_personal_records_data_type_fetches_only_personal_records(self, session, monkeypatch):
        user = _create_user(session)
        fake_client = FakeGarminClient(
            personal_records=[
                {
                    "typeId": 7,
                    "value": "42195",
                    "prStartTimeGmtFormatted": "2026-06-02",
                }
            ]
        )
        monkeypatch.setattr(pipeline, "load_user_client", lambda session, user: object())
        monkeypatch.setattr(pipeline, "GarminClient", lambda garmin: fake_client)
        monkeypatch.setattr(pipeline, "save_tokens", lambda session, user, garmin: None)

        result = pipeline.run_ingestion(
            session,
            user,
            date(2026, 6, 1),
            date(2026, 6, 1),
            data_types=["personal_records"],
        )

        assert set(result) == {"personal_records"}
        assert result["personal_records"]["rows"] == 1
        assert fake_client.calls == [("personal_records",)]

    def test_personal_records_dry_run_counts_without_writing(self, session, monkeypatch):
        user = _create_user(session)
        fake_client = FakeGarminClient(
            personal_records=[
                {
                    "typeId": 3,
                    "value": "00:22:14",
                    "prStartTimeGmtFormatted": "2026-06-01",
                }
            ]
        )
        monkeypatch.setattr(pipeline, "load_user_client", lambda session, user: object())
        monkeypatch.setattr(pipeline, "GarminClient", lambda garmin: fake_client)

        result = pipeline.run_ingestion(
            session,
            user,
            date(2026, 6, 1),
            date(2026, 6, 1),
            dry_run=True,
            data_types=["personal_records"],
        )

        records = session.scalars(select(PersonalRecord)).all()
        assert result["personal_records"]["rows"] == 1
        assert records == []

    def test_personal_record_errors_do_not_prevent_other_data_types(self, session, monkeypatch):
        user = _create_user(session)
        fake_client = FakeGarminClient(personal_error=RuntimeError("garmin unavailable"))
        monkeypatch.setattr(pipeline, "load_user_client", lambda session, user: object())
        monkeypatch.setattr(pipeline, "GarminClient", lambda garmin: fake_client)
        monkeypatch.setattr(pipeline, "save_tokens", lambda session, user, garmin: None)

        result = pipeline.run_ingestion(
            session,
            user,
            date(2026, 6, 1),
            date(2026, 6, 1),
        )

        assert result["daily_summary"]["status"] == "success"
        assert result["activities"]["status"] == "success"
        assert result["personal_records"] == {
            "status": "error",
            "rows": 0,
            "errors": 1,
        }
