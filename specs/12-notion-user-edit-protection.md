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
each page's current `properties` and `icon` in those query results for
free, so per-field comparison adds no API calls and needs no stored state.

Two rules shape the design (agreed with Mike):

- A Garmin-side rename does **not** need to propagate once the activity has
  synced. So the sync never needs to distinguish a human edit from its own
  value change: whenever the current Notion value differs from what the
  sync would write, **Notion wins**. This removes any need for a
  last-written-values table or a sync-hash field — protection requires
  reading the page's current per-field values anyway, and exact comparison
  is strictly more informative than a hash (it says *which* field differs).
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
   `Z`/`+00:00` and millisecond variants treated as equal — if either side
   is date-only, the time part is ignored. Only differing properties are
   sent to `pages.update`. If nothing differs, the update call is skipped
   and the row reports `unchanged`.
2. **Protected properties (activities only).**
   `PROTECTED_ACTIVITY_PROPERTIES = {Activity Type, Subactivity Type,
   Activity Name}` (`notion_sync/mappers.py`). A protected property whose
   current value differs from the payload is dropped from the update and
   logged at info. Corollary: future formatter changes also do not rewrite
   these three fields on existing pages.
3. **Icon (activities).** Icons are derived, never independent data. On
   update the icon is only written when the page has none; a differing icon
   means a human chose it. Daily steps and personal records (no protected
   properties) keep plain diff-only behavior: a drifted icon is refreshed.
4. **Create path.** New pages get the full payload and icon; no diffing or
   protection applies.
5. **Cover.** When a cover is supplied for an existing page, a cover-only
   change still updates the page. A matching cover does not trigger a write.

`SyncResult` gains an `unchanged` counter alongside created/updated, so a
run's summary shows how many pages needed no write.

## API-Call Impact

Per row per run, before: one query + one update (always). After: one query
for unchanged rows, one query + one update only when something actually
changed. Protection itself costs nothing — the page's current values
already arrive with the query.

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
