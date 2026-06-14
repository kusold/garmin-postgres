from datetime import date

from garmin_postgres.models.daily_summary import DailySummary


def parse_daily_summary(raw: dict, user_id: int) -> DailySummary:
    """Parse a Garmin get_user_summary() response into a DailySummary model.

    Args:
        raw: Raw API response dict from python-garminconnect.
        user_id: The database user_id to associate with this summary.

    Returns:
        A DailySummary instance ready for database persistence.

    Raises:
        KeyError: If the response is missing 'calendarDate'.
        ValueError: If 'calendarDate' is not a valid ISO date string.
    """
    calendar_date = date.fromisoformat(raw["calendarDate"])

    return DailySummary(
        user_id=user_id,
        calendar_date=calendar_date,
        raw_json=raw,
    )
