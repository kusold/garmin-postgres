from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord

from notion_sync.formatters import (
    ACTIVITY_EMOJIS,
    PERSONAL_RECORD_EMOJIS,
    PERSONAL_RECORD_NAMES,
    format_activity_type,
    format_duration,
    format_entertainment,
    format_pace,
    format_record_pace,
    format_record_value,
    format_training_effect,
    format_training_message,
    notion_date,
    number,
)


def _metric(raw: dict, key: str):
    """Read a metric from summaryDTO, falling back to the top-level key."""
    summary = raw.get("summaryDTO")
    if not isinstance(summary, dict):
        summary = {}
    return summary.get(key, raw.get(key))


def _metadata(raw: dict) -> dict:
    value = raw.get("metadataDTO")
    return value if isinstance(value, dict) else {}


def _emoji_icon(emoji: str | None) -> dict | None:
    return {"type": "emoji", "emoji": emoji} if emoji else None


# Activity properties a user may edit by hand in Notion. The sync compares
# each current value to its last written value before advancing Garmin edits.
PROTECTED_ACTIVITY_PROPERTIES = frozenset(
    {"Activity Type", "Subactivity Type", "Activity Name"}
)


def activity_filter(activity: Activity, activity_name: str, activity_type: str) -> dict:
    if activity.activity_id is not None:
        return {"property": "Garmin Activity ID", "number": {"equals": activity.activity_id}}

    return {
        "and": [
            {"property": "Date", "date": {"equals": notion_date(activity.start_time)}},
            {"property": "Activity Type", "select": {"equals": activity_type}},
            {"property": "Activity Name", "title": {"equals": activity_name}},
        ]
    }


def activity_page(activity: Activity) -> tuple[dict, dict, dict | None]:
    raw = activity.raw_json or {}
    activity_name = format_entertainment(raw.get("activityName", "Unnamed Activity"))
    type_dto = raw.get("activityTypeDTO")
    legacy_type = raw.get("activityType")
    type_key = None
    for type_payload in (type_dto, legacy_type):
        if isinstance(type_payload, dict) and type_payload.get("typeKey"):
            type_key = type_payload["typeKey"]
            break
    if not type_key:
        type_key = activity.activity_type
    activity_type, activity_subtype = format_activity_type(type_key, activity_name)

    metadata = _metadata(raw)
    # Detail payloads put the aerobic TE number in summaryDTO.trainingEffect;
    # list payloads expose it as top-level aerobicTrainingEffect.
    aerobic = _metric(raw, "trainingEffect")
    if aerobic is None:
        aerobic = _metric(raw, "aerobicTrainingEffect")
    # Same shape difference for average power: detail averagePower, list avgPower.
    avg_power = _metric(raw, "averagePower")
    if avg_power is None:
        avg_power = _metric(raw, "avgPower")
    properties = {
        "Garmin Activity ID": {"number": activity.activity_id},
        "Date": {"date": {"start": notion_date(activity.start_time or _metric(raw, "startTimeGMT"))}},
        "Activity Type": {"select": {"name": activity_type}},
        "Subactivity Type": {"select": {"name": activity_subtype}},
        "Activity Name": {"title": [{"text": {"content": activity_name}}]},
        "Distance (km)": {"number": round(number(_metric(raw, "distance")) / 1000, 2)},
        "Duration (min)": {"number": round(number(_metric(raw, "duration")) / 60, 2)},
        "Duration": {"rich_text": [{"text": {"content": format_duration(_metric(raw, "duration"))}}]},
        "Calories": {"number": round(number(_metric(raw, "calories")))},
        "Avg Pace": {"rich_text": [{"text": {"content": format_pace(_metric(raw, "averageSpeed"))}}]},
        "Avg Power": {"number": round(number(avg_power), 1)},
        "Max Power": {"number": round(number(_metric(raw, "maxPower")), 1)},
        "Training Effect": {"select": {"name": format_training_effect(_metric(raw, "trainingEffectLabel"))}},
        "Aerobic": {"number": round(number(aerobic), 1)},
        "Aerobic Effect": {"select": {"name": format_training_message(_metric(raw, "aerobicTrainingEffectMessage"))}},
        "Anaerobic": {"number": round(number(_metric(raw, "anaerobicTrainingEffect")), 1)},
        "Anaerobic Effect": {"select": {"name": format_training_message(_metric(raw, "anaerobicTrainingEffectMessage"))}},
        "PR": {"checkbox": bool(raw.get("pr") or metadata.get("personalRecord", False))},
        "Fav": {"checkbox": bool(raw.get("favorite") or metadata.get("favorite", False))},
    }

    emoji = ACTIVITY_EMOJIS.get(activity_subtype if activity_subtype != activity_type else activity_type)
    return properties, activity_filter(activity, activity_name, activity_type), _emoji_icon(emoji)


def daily_steps_page(summary: DailySummary) -> tuple[dict, dict, dict | None]:
    raw = summary.raw_json or {}
    total_distance = raw.get("totalDistance") or raw.get("totalDistanceMeters") or 0
    properties = {
        "Activity Type": {"title": [{"text": {"content": "Walking"}}]},
        "Date": {"date": {"start": summary.calendar_date.isoformat()}},
        "Total Steps": {"number": raw.get("totalSteps")},
        "Step Goal": {"number": raw.get("stepGoal") or raw.get("totalStepsGoal") or raw.get("dailyStepGoal")},
        "Total Distance (km)": {"number": round(total_distance / 1000, 2)},
    }
    filter_payload = {
        "and": [
            {"property": "Date", "date": {"equals": summary.calendar_date.isoformat()}},
            {"property": "Activity Type", "title": {"equals": "Walking"}},
        ]
    }
    return properties, filter_payload, _emoji_icon(ACTIVITY_EMOJIS["Walking"])


def personal_record_name(record: PersonalRecord) -> str:
    return PERSONAL_RECORD_NAMES.get(record.type_id, "Unnamed Activity")


def personal_record_page(record: PersonalRecord) -> tuple[dict, dict, dict | None]:
    raw = record.raw_json or {}
    name = personal_record_name(record)
    value = record.value_text
    # PR payloads carry no pace key; derive it from the duration value and the
    # known race distance of the record type.
    pace = format_record_pace(record.type_id, value)
    properties = {
        "Date": {"date": {"start": record.record_date.isoformat()}},
        "Activity Type": {"select": {"name": format_activity_type(record.activity_type)[0]}},
        "Record": {"title": [{"text": {"content": name}}]},
        "typeId": {"number": record.type_id},
        "Value": {"rich_text": [{"text": {"content": format_record_value(record.type_id, value)}}]},
        "Pace": {"rich_text": [{"text": {"content": pace}}]},
        "PR": {"checkbox": True},
    }
    filter_payload = {
        "property": "typeId",
        "number": {"equals": record.type_id},
    }
    return properties, filter_payload, _emoji_icon(PERSONAL_RECORD_EMOJIS.get(record.type_id))
