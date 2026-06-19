from __future__ import annotations

from dataclasses import dataclass


DAILY_SUMMARY = "daily_summary"
ACTIVITIES = "activities"
PERSONAL_RECORDS = "personal_records"

DEFAULT_DATA_TYPES = [DAILY_SUMMARY, ACTIVITIES, PERSONAL_RECORDS]


@dataclass(frozen=True)
class IngestObject:
    canonical_name: str
    cli_name: str
    aliases: tuple[str, ...]


INGEST_OBJECTS = (
    IngestObject(
        canonical_name=DAILY_SUMMARY,
        cli_name="daily-summary",
        aliases=("daily-summary", "daily_summary", "daily"),
    ),
    IngestObject(
        canonical_name=ACTIVITIES,
        cli_name="activities",
        aliases=("activities", "activity"),
    ),
    IngestObject(
        canonical_name=PERSONAL_RECORDS,
        cli_name="personal-records",
        aliases=("personal-records", "personal_records", "records", "prs"),
    ),
)

_ALIASES = {
    alias: ingest_object.canonical_name
    for ingest_object in INGEST_OBJECTS
    for alias in ingest_object.aliases
}
_CLI_NAMES = {
    ingest_object.canonical_name: ingest_object.cli_name
    for ingest_object in INGEST_OBJECTS
}


class UnknownIngestObject(ValueError):
    pass


def normalize_data_type(data_type: str) -> str:
    normalized = data_type.strip().lower().replace(" ", "-")
    try:
        return _ALIASES[normalized]
    except KeyError as exc:
        valid = ", ".join(sorted(_ALIASES))
        raise UnknownIngestObject(
            f"Unknown ingest data type {data_type!r}. Valid values: {valid}"
        ) from exc


def normalize_data_types(data_types: list[str] | None) -> list[str]:
    if data_types is None:
        return list(DEFAULT_DATA_TYPES)

    normalized: list[str] = []
    for data_type in data_types:
        canonical = normalize_data_type(data_type)
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def cli_name(data_type: str) -> str:
    return _CLI_NAMES[normalize_data_type(data_type)]
