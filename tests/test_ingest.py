from datetime import date, datetime, timezone

from sqlalchemy import select

from garmin_postgres.ingest.pipeline import upsert_activity, upsert_activity_file, upsert_daily_summary
from garmin_postgres.models.activity import Activity
from garmin_postgres.models.activity_file import ActivityFile
from garmin_postgres.models.daily_summary import DailySummary
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
