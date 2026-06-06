from datetime import date

from sqlalchemy import select

from garmin_postgres.ingest.pipeline import upsert_daily_summary
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
