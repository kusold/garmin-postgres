"""Diff mapper payloads against a Notion page's current values.

The sync already queries Notion for each row's existing page; these pure
helpers turn the returned page into a minimal update. Unchanged pages are
not rewritten, and human edits to protected properties are never
overwritten — whenever the current Notion value differs from what the sync
would write, Notion wins.
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
    return our_when == their_when


def _plan_icon(existing_page: dict, icon: dict | None, *, protect: bool) -> dict | None:
    """Icons are derived, never independent data. With protected properties
    (activities) a differing icon means a human chose it, so only a missing
    icon is filled in; otherwise a drifted icon is refreshed."""
    if icon is None:
        return None
    current_icon = existing_page.get("icon")
    if not isinstance(current_icon, dict):
        return icon
    kind = icon.get("type")
    if current_icon.get("type") == kind and current_icon.get(kind) == icon.get(kind):
        return None  # already matches
    return None if protect else icon


def plan_update(
    existing_page: dict,
    properties: dict,
    icon: dict | None = None,
    protected: Collection[str] = frozenset(),
) -> tuple[dict, dict | None, str]:
    """Decide the minimal update for an existing page.

    Returns ``(changed_properties, icon_or_none, action)`` where ``action``
    is ``"updated"`` when something needs writing, else ``"unchanged"``.
    Properties whose current value matches the payload are dropped from the
    update; a protected property whose value differs is left untouched
    (Notion wins), and a protected data type never has its icon overwritten.
    """
    current = existing_page.get("properties") or {}
    changed: dict[str, Any] = {}
    for name, payload_prop in properties.items():
        if _values_equal(
            _property_value(payload_prop), _property_value(current.get(name))
        ):
            continue
        if name in protected:
            logger.info(
                "Preserving Notion value of %r on page %s; it differs from the synced value",
                name,
                existing_page.get("id"),
            )
            continue
        changed[name] = payload_prop
    icon_to_write = _plan_icon(existing_page, icon, protect=bool(protected))
    action = "updated" if changed or icon_to_write else "unchanged"
    return changed, icon_to_write, action
