from datetime import date
from typing import Any

from garmin_postgres.models.personal_record import PersonalRecord


def _parse_record_date(value: Any) -> date | None:
    if value is None:
        return None
    value_text = str(value)
    if not value_text:
        return None
    return date.fromisoformat(value_text[:10])


def _parse_activity_type(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        type_key = value.get("typeKey")
        return str(type_key) if type_key is not None else None
    return str(value)


def parse_personal_record(raw: dict, user_id: int) -> PersonalRecord:
    """Parse a Garmin personal record response into a PersonalRecord model.

    Args:
        raw: Raw personal record dict from get_personal_record().
             Expected key: typeId.
        user_id: The database user_id to associate with this record.

    Returns:
        A PersonalRecord instance ready for database persistence.

    Raises:
        KeyError: If 'typeId' is missing.
        ValueError: If 'typeId' or 'prStartTimeGmtFormatted' cannot be parsed.
    """
    type_id = int(raw["typeId"])
    record_date = _parse_record_date(raw.get("prStartTimeGmtFormatted"))
    activity_type = _parse_activity_type(raw.get("activityType"))
    value = raw.get("value")

    return PersonalRecord(
        user_id=user_id,
        type_id=type_id,
        record_date=record_date,
        activity_type=activity_type,
        value_text=str(value) if value is not None else None,
        raw_json=raw,
    )


def parse_personal_records(raw_records: list[dict], user_id: int) -> list[PersonalRecord]:
    """Parse a list of Garmin personal record responses."""
    return [parse_personal_record(raw, user_id) for raw in raw_records]
