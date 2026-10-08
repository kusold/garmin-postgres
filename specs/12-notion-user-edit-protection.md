# Notion User-Edit Protection

## Goal

Katie occasionally edits **Activity Type**, **Subactivity Type**, and
**Activity Name** on activity pages directly in Notion. Those edits must
persist — the sync must not revert them on its twice-daily runs. Related:
unchanged pages should not be rewritten at all, so Notion's page history
stops showing phantom edits every run and the sync makes fewer API calls.

## Background

`NotionSink.upsert_page` (`notion_sync/notion.py`) already queries the
Notion database for each row's existing page, but used the result only for
its page ID and overwrote every property via `pages.update`. Notion returns
each page's current `properties` and `icon` in those query results. Comparing
them with values the sync last wrote needs persistent sync state, but adds no
Notion API calls.

The original implementation used these rules:

- Whenever the current Notion value differed from the sync payload,
  **Notion won**. This also blocked Garmin-side renames of pages that had
  never been edited in Notion.
- The icon is derived from the activity type. When the type fields are
  human-edited, the derived icon must not be written.

## Semantics

Property and icon diffing lives in `notion_sync/updates.py` as pure
functions, called from the sink's update path. The sink also compares a
supplied cover with the page's current cover.

1. **Diff-only updates (all data types).** Each property in the mapper
   payload is compared against the page's current value, normalized per
   type: title/rich_text compare joined plain text, select compares the
   option name, number compares the number (int/float equal), checkbox the
   boolean, and dates compare parsed instants (naive treated as UTC) with
   `Z`/`+00:00` variants treated as equal. Timed dates compare to the minute,
   because Notion drops seconds; if either side is date-only, the time part
   is ignored. Only differing properties are
   sent to `pages.update`. If nothing differs, the update call is skipped
   and the row reports `unchanged`.
2. **Protected properties (activities only).**
   `PROTECTED_ACTIVITY_PROPERTIES = {Activity Type, Subactivity Type,
   Activity Name}` (`notion_sync/mappers.py`). The sync stores its last written
   value per destination item in `destination_sync_states`. The table stores
   opaque JSON and uses a destination namespace plus item ID as its key;
   the Notion adapter decides the JSON shape. A new Garmin value replaces the
   current value only when the current value still matches that baseline.
   A differing Notion value is preserved. Legacy pages without a baseline
   can advance when their creation and last-edit timestamps match; a matching
   field also seeds its baseline. A legacy page with an unexplained difference
   remains protected.
3. **Icon (activities).** The last written icon is tracked alongside the
   protected properties, so a Garmin activity-type edit can update an
   untouched icon. A human-chosen icon is preserved. Daily steps and personal
   records keep plain diff-only behavior.
4. **Create path.** New pages get the full payload and icon, and establish the
   first last-written baseline.
5. **Cover.** When a cover is supplied for an existing page, a cover-only
   change still updates the page. A matching cover does not trigger a write.

`SyncResult` gains an `unchanged` counter alongside created/updated, so a
run's summary shows how many pages needed no write.

## API-Call Impact

Per row per run, before: one query + one update (always). After: one query
for unchanged rows, one query + one update only when something actually
changed. Protection adds local state reads and writes, but no Notion API calls:
the page's current values already arrive with the query.

## Out of Scope

- The null-`activity_id` fallback filter (`activity_filter` in
  `notion_sync/mappers.py`) matches on Date + Activity Type + Activity
  Name; a page whose name/type was human-edited would not match and would
  be re-created. Pre-existing behavior; Garmin-ingested activities always
  carry an activity ID.
- Collapsing the per-row queries into one batched query per database per
  run (a sink refactor that would cut calls further).

## Testing

- `plan_update` unit tests (pure, no Notion): matching page → `unchanged`;
  only differing unprotected properties sent; human-edited protected
  property dropped (alone and alongside another change); differing
  activity icon never overwritten, missing icon filled, drifted icon
  refreshed without protection; date ISO variants equal, different date
  detected.
- Sink tests with the fake client: unchanged page issues no `pages.update`;
  user-edited name issues no write; only the changed property is sent;
  changed and matching covers take the expected update paths.
- Sync tests: `sync_activities` forwards the protected set and counts
  `unchanged`; `sync_daily_steps` forwards none.
