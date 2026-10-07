from datetime import datetime, timezone
from typing import Any


ACTIVITY_ICONS = {
    "Barre": "https://img.icons8.com/?size=100&id=66924&format=png&color=000000",
    "Breathwork": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Cardio": "https://img.icons8.com/?size=100&id=71221&format=png&color=000000",
    "Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Hiking": "https://img.icons8.com/?size=100&id=9844&format=png&color=000000",
    "Indoor Cardio": "https://img.icons8.com/?size=100&id=62779&format=png&color=000000",
    "Indoor Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Indoor Rowing": "https://img.icons8.com/?size=100&id=71098&format=png&color=000000",
    "Pilates": "https://img.icons8.com/?size=100&id=9774&format=png&color=000000",
    "Meditation": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Rowing": "https://img.icons8.com/?size=100&id=71491&format=png&color=000000",
    "Running": "https://img.icons8.com/?size=100&id=k1l1XFkME39t&format=png&color=000000",
    "Strength Training": "https://img.icons8.com/?size=100&id=107640&format=png&color=000000",
    "Stretching": "https://img.icons8.com/?size=100&id=djfOcRn1m_kh&format=png&color=000000",
    "Swimming": "https://img.icons8.com/?size=100&id=9777&format=png&color=000000",
    "Treadmill Running": "https://img.icons8.com/?size=100&id=9794&format=png&color=000000",
    "Walking": "https://img.icons8.com/?size=100&id=9807&format=png&color=000000",
    "Yoga": "https://img.icons8.com/?size=100&id=9783&format=png&color=000000",
}


# Garmin emits one row per day for this type (a running daily-streak counter),
# so many rows share it; every other type arrives as a single best record.
DAILY_STREAK_TYPE_ID = 16


PERSONAL_RECORD_NAMES = {
    1: "1K",
    2: "1mi",
    3: "5K",
    4: "10K",
    5: "Half Marathon",
    6: "Marathon",
    7: "Longest Run",
    8: "Longest Ride",
    9: "Total Ascent",
    10: "Max Avg Power (20 min)",
    11: "Fastest 40 km",
    12: "Most Steps in a Day",
    13: "Most Steps in a Week",
    14: "Most Steps in a Month",
    15: "Longest Goal Streak",
    16: "Daily Streak",
}


# Race distances in km for duration-type records (value is seconds), used to
# derive pace. Distance-type records (longest run/ride, steps, streaks) and
# unknown types have no derivable pace.
PERSONAL_RECORD_DISTANCES_KM = {
    1: 1.0,
    2: 1.609344,
    3: 5.0,
    4: 10.0,
    5: 21.0975,
    6: 42.195,
}


def format_record_pace(type_id: int, value_text: str | None) -> str:
    distance = PERSONAL_RECORD_DISTANCES_KM.get(type_id)
    if not distance or not value_text:
        return ""
    try:
        total_seconds = float(value_text)
    except ValueError:
        return ""
    if total_seconds <= 0:
        return ""
    pace_min_km = total_seconds / 60 / distance
    minutes = int(pace_min_km)
    seconds = int((pace_min_km - minutes) * 60)
    return f"{minutes}:{seconds:02d} min/km"


def format_activity_type(activity_type: str | None, activity_name: str = "") -> tuple[str, str]:
    formatted_type = activity_type.replace("_", " ").title() if activity_type else "Unknown"
    activity_type_name = formatted_type
    activity_subtype = formatted_type

    activity_mapping = {
        "Barre": "Strength",
        "Indoor Cardio": "Cardio",
        "Indoor Cycling": "Cycling",
        "Indoor Rowing": "Rowing",
        "Speed Walking": "Walking",
        "Strength Training": "Strength",
        "Treadmill Running": "Running",
    }

    if formatted_type == "Rowing V2":
        activity_type_name = "Rowing"
    elif formatted_type in ["Yoga", "Pilates"]:
        activity_type_name = "Yoga/Pilates"

    if formatted_type in activity_mapping:
        activity_type_name = activity_mapping[formatted_type]
        activity_subtype = formatted_type

    lowered = activity_name.lower()
    if "meditation" in lowered:
        return "Meditation", "Meditation"
    if "barre" in lowered:
        return "Strength", "Barre"
    if "stretch" in lowered:
        return "Stretching", "Stretching"

    return activity_type_name, activity_subtype


def format_entertainment(activity_name: str) -> str:
    return activity_name.replace("ENTERTAINMENT", "Netflix")


def format_training_message(message: str | None) -> str:
    if not message:
        return "Unknown"
    messages = {
        "NO_": "No Benefit",
        "MINOR_": "Some Benefit",
        "RECOVERY_": "Recovery",
        "MAINTAINING_": "Maintaining",
        "IMPROVING_": "Impacting",
        "IMPACTING_": "Impacting",
        "HIGHLY_": "Highly Impacting",
        "OVERREACHING_": "Overreaching",
    }
    for key, value in messages.items():
        if message.startswith(key):
            return value
    return message


def format_training_effect(training_effect_label: str | None) -> str:
    return (training_effect_label or "Unknown").replace("_", " ").title()


def format_pace(average_speed: float | int | None) -> str:
    if average_speed and average_speed > 0:
        pace_min_km = 1000 / (average_speed * 60)
        minutes = int(pace_min_km)
        seconds = int((pace_min_km - minutes) * 60)
        return f"{minutes}:{seconds:02d} min/km"
    return ""


def number(value: Any, default: float = 0) -> float:
    return default if value is None else value


def notion_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    text = str(value)
    if " " in text and "T" not in text:
        return text.replace(" ", "T")
    return text
