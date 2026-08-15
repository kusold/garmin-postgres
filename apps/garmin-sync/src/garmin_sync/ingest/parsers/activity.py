from datetime import datetime, timezone

from garmin_postgres.models.activity import Activity


def _parse_garmin_utc(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _activity_type(raw: dict) -> str | None:
    """Extract an activity type from either Garmin activity response shape."""
    for key in ("activityType", "activityTypeDTO"):
        value = raw.get(key)
        if isinstance(value, dict):
            type_key = value.get("typeKey")
            if type_key is not None:
                return str(type_key)
    return None


def _start_time_gmt(raw: dict) -> str | None:
    """Extract the UTC start time from an activity or its nested summary."""
    start_time = raw.get("startTimeGMT")
    if start_time is not None:
        return str(start_time)

    summary = raw.get("summaryDTO")
    if isinstance(summary, dict):
        start_time = summary.get("startTimeGMT")
        if start_time is not None:
            return str(start_time)
    return None


def parse_activity(raw: dict, user_id: int) -> Activity:
    """Parse a Garmin activity response into an Activity model.

    Args:
        raw: Raw activity dict from get_activity() (detailed) or
             get_activities_by_date() (summary).
             Expected keys: activityId plus activity type and start time fields.
             Garmin returns these either as activityType.typeKey/startTimeGMT or
             activityTypeDTO.typeKey/summaryDTO.startTimeGMT.
        user_id: The database user_id to associate with this activity.

    Returns:
        An Activity instance ready for database persistence.

    Raises:
        KeyError: If 'activityId' is missing.
    """
    activity_id = raw["activityId"]
    activity_type = _activity_type(raw)

    start_time = None
    start_time_gmt = _start_time_gmt(raw)
    if start_time_gmt is not None:
        start_time = _parse_garmin_utc(start_time_gmt)

    return Activity(
        user_id=user_id,
        activity_id=activity_id,
        activity_type=activity_type,
        start_time=start_time,
        raw_json=raw,
    )
