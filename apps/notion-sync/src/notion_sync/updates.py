"""Diff mapper payloads against a Notion page's current values.

The sync already queries Notion for each row's existing page; these pure
helpers turn the returned page into a minimal update. Unchanged pages are
not rewritten. A protected value advances only while it matches the value
last written by the sync, or on a legacy page untouched since creation.
"""

import logging
from collections.abc import Collection
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _block_text(blocks: Any) -> str:
    """Concatenate text blocks in either the payload or the returned shape."""
    if not blocks:
        return ""
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if "plain_text" in block:
            parts.append(block["plain_text"])
        else:
            parts.append(block.get("text", {}).get("content", ""))
    return "".join(parts)


def _date_value(start: Any) -> tuple[datetime | str | None, bool]:
    """Canonical date: the parsed instant (naive treated as UTC) plus whether
    the original text carried a time part."""
    if not isinstance(start, str) or not start:
        return (start, False)
    text = start.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return (start, False)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed, "T" in text)


def _property_value(prop: dict | None) -> Any:
    """Comparable value for a property in either the mapper's write-payload
    shape or Notion's returned shape (Notion only adds keys like ``type``)."""
    if not isinstance(prop, dict):
        return None
    if "number" in prop:
        return prop["number"]
    if "checkbox" in prop:
        return bool(prop["checkbox"])
    if "select" in prop:
        select = prop["select"]
        return select.get("name") if isinstance(select, dict) else select
    if "title" in prop:
        return _block_text(prop["title"])
    if "rich_text" in prop:
        return _block_text(prop["rich_text"])
    if "date" in prop:
        date = prop["date"]
        return _date_value(date.get("start")) if isinstance(date, dict) else (None, False)
    return prop


def _values_equal(ours: Any, theirs: Any) -> bool:
    if isinstance(ours, tuple) and isinstance(theirs, tuple):
        return _dates_equal(ours, theirs)
    return ours == theirs


def _dates_equal(ours: tuple, theirs: tuple) -> bool:
    (our_when, our_has_time), (their_when, their_has_time) = ours, theirs
    # Unparseable or absent values keep plain comparison.
    if (
        not isinstance(our_when, datetime)
        or not isinstance(their_when, datetime)
    ):
        return our_when == their_when
    # A date-only side ignores the time part (Notion may drop the time).
    if not (our_has_time and their_has_time):
        return our_when.date() == their_when.date()
    # Notion date properties retain minute precision and drop seconds.
    return our_when.replace(second=0, microsecond=0) == their_when.replace(
        second=0, microsecond=0
    )


def _icons_equal(current: dict | None, desired: dict | None) -> bool:
    if not isinstance(current, dict) or not isinstance(desired, dict):
        return current == desired
    kind = desired.get("type")
    return current.get("type") == kind and current.get(kind) == desired.get(kind)


def _plan_icon(
    existing_page: dict,
    icon: dict | None,
    *,
    protect: bool,
    baseline: dict | None,
    pristine: bool,
) -> dict | None:
    """Refresh derived icons unless the page icon was changed in Notion."""
    if icon is None:
        return None
    current_icon = existing_page.get("icon")
    if not isinstance(current_icon, dict):
        return icon
    if _icons_equal(current_icon, icon):
        return None  # already matches
    if protect and not (_icons_equal(current_icon, baseline) or (pristine and baseline is None)):
        return None
    return icon


def plan_update(
    existing_page: dict,
    properties: dict,
    icon: dict | None = None,
    protected: Collection[str] = frozenset(),
    protected_baseline: dict[str, Any] | None = None,
    icon_baseline: dict | None = None,
    pristine: bool = False,
) -> tuple[dict, dict | None, str]:
    """Decide the minimal update for an existing page.

    Returns ``(changed_properties, icon_or_none, action)`` where ``action``
    is ``"updated"`` when something needs writing, else ``"unchanged"``.
    A protected property advances when its current value still equals the
    last value written by the sync. Legacy pages with no baseline may also
    advance if they have never been edited since creation.
    """
    current = existing_page.get("properties") or {}
    changed: dict[str, Any] = {}
    protected_type_retained = False
    for name, payload_prop in properties.items():
        if _values_equal(
            _property_value(payload_prop), _property_value(current.get(name))
        ):
            continue
        last_written = protected_baseline.get(name) if protected_baseline else None
        has_baseline = protected_baseline is not None and name in protected_baseline
        can_advance = (
            (has_baseline and _values_equal(_property_value(current.get(name)), last_written))
            or (pristine and not has_baseline)
        )
        if name in protected and not can_advance:
            if name in {"Activity Type", "Subactivity Type"}:
                protected_type_retained = True
            logger.info(
                "Preserving Notion value of %r on page %s; it differs from the last synced value",
                name,
                existing_page.get("id"),
            )
            continue
        changed[name] = payload_prop
    icon_to_write = None if protected_type_retained else _plan_icon(
        existing_page, icon, protect=bool(protected),
        baseline=icon_baseline, pristine=pristine,
    )
    action = "updated" if changed or icon_to_write else "unchanged"
    return changed, icon_to_write, action
