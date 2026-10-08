import time
from collections.abc import Collection
from typing import Any, Callable

from notion_sync.updates import plan_update


class NotionSink:
    """Writes rows to a Notion database with client-side pacing.

    The Notion client owns method-aware retries. This class paces calls and
    resolves each configured database to its data source once per run.

    Updates are minimal: the query already returns each existing page, so
    ``plan_update`` diffs the payload against the page and only changed
    properties are sent. Pages whose values all match are not rewritten, and
    protected properties a user edited in Notion are never overwritten.

    Since Notion API version 2025-09-03 (notion-client 3.x), databases are
    containers whose rows belong to data sources. Configured database IDs are
    container IDs, so they are resolved to a data source ID via
    ``databases.retrieve`` (cached per database) before querying or creating
    pages.
    """

    def __init__(
        self,
        client: Any,
        *,
        dry_run: bool = False,
        min_interval: float = 0.34,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.dry_run = dry_run
        # Minimum seconds between successive Notion API calls (pacing).
        self.min_interval = min_interval
        # Injectable timing primitives so tests can avoid real delays.
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call_at: float | None = None
        # database_id -> resolved data_source_id, so discovery happens once
        # per configured database per run.
        self._data_source_ids: dict[str, str] = {}

    def _invoke(self, fn: Callable[..., Any], /, **kwargs: Any) -> Any:
        """Pace the next client call; the client handles retries."""
        if self.min_interval > 0 and self._last_call_at is not None:
            elapsed = self._monotonic() - self._last_call_at
            remaining = self.min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)

        self._last_call_at = self._monotonic()
        return fn(**kwargs)

    def _resolve_data_source_id(self, database_id: str) -> str:
        """Map a database (container) ID to the ID of its single data source."""
        if database_id not in self._data_source_ids:
            database = self._invoke(
                self.client.databases.retrieve,
                database_id=database_id,
            )
            data_sources = database["data_sources"]
            if not data_sources:
                raise ValueError(
                    f"Notion database {database_id} has no data sources"
                )
            if len(data_sources) > 1:
                raise ValueError(
                    f"Notion database {database_id} has multiple data sources "
                    f"({', '.join(ds['id'] for ds in data_sources)}); "
                    "sync requires a database with a single data source"
                )
            self._data_source_ids[database_id] = data_sources[0]["id"]
        return self._data_source_ids[database_id]

    def upsert_page(
        self,
        database_id: str,
        *,
        filter_payload: dict,
        properties: dict,
        icon: dict | None = None,
        cover: dict | None = None,
        protected: Collection[str] = frozenset(),
    ) -> str:
        data_source_id = self._resolve_data_source_id(database_id)
        existing = self._invoke(
            self.client.data_sources.query,
            data_source_id=data_source_id,
            filter=filter_payload,
        )["results"]

        if self.dry_run:
            return "dry_run"

        if existing:
            page = existing[0]
            changed, icon_to_write, action = plan_update(
                page, properties, icon=icon, protected=protected
            )
            cover_to_write = cover if cover and cover != page.get("cover") else None
            if action == "unchanged" and cover_to_write is None:
                return "unchanged"
            update_payload: dict[str, Any] = {
                "page_id": page["id"],
                "properties": changed,
            }
            if icon_to_write:
                update_payload["icon"] = icon_to_write
            if cover_to_write:
                update_payload["cover"] = cover_to_write
            self._invoke(self.client.pages.update, **update_payload)
            return "updated"

        create_payload: dict[str, Any] = {
            "parent": {"data_source_id": data_source_id},
            "properties": properties,
        }
        if icon:
            create_payload["icon"] = icon
        if cover:
            create_payload["cover"] = cover
        self._invoke(self.client.pages.create, **create_payload)
        return "created"
