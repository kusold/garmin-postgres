from garmin_postgres.models.activity import Activity
from garmin_postgres.models.daily_summary import DailySummary
from garmin_postgres.models.personal_record import PersonalRecord

from notion_sync.formatters import (
    ACTIVITY_ICONS,
    PERSONAL_RECORD_NAMES,
    format_activity_type,
    format_entertainment,
    format_pace,
    format_training_effect,
    format_training_message,
    notion_date,
    number,
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
    activity_type, activity_subtype = format_activity_type(
        raw.get("activityType", {}).get("typeKey") if isinstance(raw.get("activityType"), dict) else activity.activity_type,
        activity_name,
    )

    properties = {
        "Garmin Activity ID": {"number": activity.activity_id},
        "Date": {"date": {"start": notion_date(activity.start_time or raw.get("startTimeGMT"))}},
        "Activity Type": {"select": {"name": activity_type}},
        "Subactivity Type": {"select": {"name": activity_subtype}},
        "Activity Name": {"title": [{"text": {"content": activity_name}}]},
        "Distance (km)": {"number": round(number(raw.get("distance")) / 1000, 2)},
        "Duration (min)": {"number": round(number(raw.get("duration")) / 60, 2)},
        "Calories": {"number": round(number(raw.get("calories")))},
        "Avg Pace": {"rich_text": [{"text": {"content": format_pace(raw.get("averageSpeed"))}}]},
        "Avg Power": {"number": round(number(raw.get("avgPower")), 1)},
        "Max Power": {"number": round(number(raw.get("maxPower")), 1)},
        "Training Effect": {"select": {"name": format_training_effect(raw.get("trainingEffectLabel"))}},
        "Aerobic": {"number": round(number(raw.get("aerobicTrainingEffect")), 1)},
        "Aerobic Effect": {"select": {"name": format_training_message(raw.get("aerobicTrainingEffectMessage"))}},
        "Anaerobic": {"number": round(number(raw.get("anaerobicTrainingEffect")), 1)},
        "Anaerobic Effect": {"select": {"name": format_training_message(raw.get("anaerobicTrainingEffectMessage"))}},
        "PR": {"checkbox": bool(raw.get("pr", False))},
        "Fav": {"checkbox": bool(raw.get("favorite", False))},
    }

    icon_url = ACTIVITY_ICONS.get(activity_subtype if activity_subtype != activity_type else activity_type)
    icon = {"type": "external", "external": {"url": icon_url}} if icon_url else None
    return properties, activity_filter(activity, activity_name, activity_type), icon


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
    return properties, filter_payload, None


def personal_record_name(record: PersonalRecord) -> str:
    return PERSONAL_RECORD_NAMES.get(record.type_id, "Unnamed Activity")


def personal_record_page(record: PersonalRecord) -> tuple[dict, dict, dict | None]:
    raw = record.raw_json or {}
    name = personal_record_name(record)
    value = record.value_text
    pace = str(raw.get("pace") or "")
    properties = {
        "Date": {"date": {"start": record.record_date.isoformat()}},
        "Activity Type": {"select": {"name": format_activity_type(record.activity_type)[0]}},
        "Record": {"title": [{"text": {"content": name}}]},
        "typeId": {"number": record.type_id},
        "Value": {"rich_text": [{"text": {"content": value}}]},
        "Pace": {"rich_text": [{"text": {"content": pace}}]},
        "PR": {"checkbox": True},
    }
    filter_payload = {
        "and": [
            {"property": "Record", "title": {"equals": name}},
            {"property": "Date", "date": {"equals": record.record_date.isoformat()}},
        ]
    }
    return properties, filter_payload, None
