from datetime import datetime, timezone

from garmin_postgres.models.activity import Activity


def _parse_garmin_utc(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_activity(raw: dict, user_id: int) -> Activity:
    """Parse a Garmin activity response into an Activity model.

    Args:
        raw: Raw activity dict from get_activities_by_date().
             Expected keys: activityId, activityType.typeKey, startTimeGMT.
        user_id: The database user_id to associate with this activity.

    Returns:
        An Activity instance ready for database persistence.

    Raises:
        KeyError: If 'activityId' is missing.
    """
    activity_id = raw["activityId"]
    activity_type = raw.get("activityType", {}).get("typeKey")

    start_time = None
    if "startTimeGMT" in raw:
        start_time = _parse_garmin_utc(raw["startTimeGMT"])

    return Activity(
        user_id=user_id,
        activity_id=activity_id,
        activity_type=activity_type,
        start_time=start_time,
        raw_json=raw,
    )
