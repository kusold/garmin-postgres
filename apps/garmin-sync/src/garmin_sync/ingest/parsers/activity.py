from datetime import datetime, timezone

from garmin_postgres.models.activity import Activity


def _parse_garmin_utc(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _payload_dict(raw: dict, key: str) -> dict:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def parse_activity(raw: dict, user_id: int) -> Activity:
    """Parse a Garmin activity response into an Activity model.

    Args:
        raw: Raw activity dict from get_activity() (detailed) or
             get_activities_by_date() (summary). Handles both the
             summaryDTO/activityTypeDTO nesting returned by list endpoints
             and the legacy top-level startTimeGMT/activityType.typeKey
             shape returned by detail endpoints.
        user_id: The database user_id to associate with this activity.

    Returns:
        An Activity instance ready for database persistence.

    Raises:
        KeyError: If 'activityId' is missing.
    """
    activity_id = raw["activityId"]

    summary = _payload_dict(raw, "summaryDTO")
    type_dto = _payload_dict(raw, "activityTypeDTO")
    legacy_type = _payload_dict(raw, "activityType")
    activity_type = type_dto.get("typeKey") or legacy_type.get("typeKey")

    start_raw = summary.get("startTimeGMT") or raw.get("startTimeGMT")
    start_time = _parse_garmin_utc(start_raw) if start_raw else None

    return Activity(
        user_id=user_id,
        activity_id=activity_id,
        activity_type=activity_type,
        start_time=start_time,
        raw_json=raw,
    )
