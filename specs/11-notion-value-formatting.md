# Notion Value Formatting

## Goal

Make the values synced to Notion readable by humans. Personal-record pages
currently show Garmin's raw numbers (`9876.0` for a marathon), and activity
durations show decimal minutes (`62.35`). Formatting happens in notion-sync
before pushing; activities additionally switch to imperial units (miles,
min/mi). Personal records stay metric.

## Background

The Notion API offers no way to render these values after the fact: number
properties support only comma / percent / currency display formats, and
formula properties cannot be written via the API at all (they must be
hand-built in the Notion UI). So the only workable approach is to format
values into strings before pushing — the same approach used by the reference
project [kburgert/garmin-to-notion](https://github.com/kburgert/garmin-to-notion),
whose `format_garmin_value(value, type, typeId)` maps each record type to a
human string (`h:mm:ss` durations, `42.19 km`, `265 W`, comma-grouped steps,
`X days` streaks).

All formatting lives in `notion_sync/formatters.py`, which already holds
`format_pace`, `format_record_pace`, and the type-ID lookup tables. This
project's convention that parsers/formatters are pure functions keeps the new
code trivially testable.

## Personal Records (metric, unchanged units)

`personal_record_page` sends `format_record_value(type_id, value_text)` in the
existing `Value` text property instead of the raw `value_text`. Fractional
seconds round to whole seconds before formatting.

| typeId | Record | Raw example | Formatted `Value` |
| --- | --- | --- | --- |
| 1 | 1K | `178.0` | `2:58` |
| 2 | 1mi | `292.0` | `4:52` |
| 3 | 5K | `1074.0` | `17:54` |
| 4 | 10K | `2236.0` | `37:16` |
| 5 | Half Marathon | `5012.0` | `1:23:32` |
| 6 | Marathon | `9876.0` | `2:44:36` |
| 7 | Longest Run | `32165.0` | `32.17 km` |
| 8 | Longest Ride | `73540.0` | `73.54 km` |
| 9 | Total Ascent | `1234.0` | `1,234 m` |
| 10 | Max Avg Power (20 min) | `265.0` | `265 W` |
| 11 | Fastest 40 km | `5421.0` | `1:30:21` |
| 12–14 | Most Steps day/week/month | `27106` | `27,106` |
| 15 | Longest Goal Streak | `42.0` | `42 days` |
| 16 | Daily Streak | `137.0` | `137 days` |

Durations use `m:ss` under an hour and `h:mm:ss` at or above.

`Pace` keeps its current `min/km` output for types 1–6. Type 11 (Fastest
40 km, a cycling record) gains a pace formatted as `26.6 km/h` — `min/km`
reads wrongly for rides. Distance-type, step, streak, and unknown records
keep a blank pace.

## Activities (imperial)

`activity_page` changes:

- `Distance (km)` becomes `Distance (mi)` — meters / 1609.344, rounded to
  2 decimals.
- `Avg Pace` becomes minutes per mile — `1609.344 / (m/s × 60)` rendered in
  the existing `m:ss min/mi` shape.
- `Duration (min)` (number) stays for numeric sorting, and a new `Duration`
  text property carries `format_duration(seconds)` (`1:02:21`).

`daily_steps_page` writes rows into the same database, so its
`Total Distance (km)` becomes `Total Distance (mi)` for consistency.

All other activity columns (calories, power in watts, training effect) are
unit-neutral or already readable and are untouched.

## Formatter Functions

- `format_duration(seconds)` — `m:ss` under an hour, `h:mm:ss` at or above;
  `""` for `None` or ≤ 0; rounds fractional seconds first.
- `format_record_value(type_id, value_text)` — the PR table above. Reuses
  `format_duration` for types 1–6 and 11. Unknown `type_id` or unparseable
  value falls back to the raw string unchanged so data never disappears
  (mirroring `format_record_pace`'s degradation).
- `format_record_pace` gains a 40 km entry for type 11 and the `km/h` branch.
- `format_pace` switches to min/mi (activities are its only caller).

## One-Time Notion Setup (manual)

The API does not auto-create properties, and `NotionSink` deliberately has no
schema-management role, so the activities database gets a one-time UI edit:

- Add a text property named `Duration`.
- Rename `Distance (km)` → `Distance (mi)` and
  `Total Distance (km)` → `Total Distance (mi)` (renaming preserves the
  properties; values convert on the next sync).

The PR database needs no changes — `Value` and `Pace` are already text
properties.

## Backfill

None needed. `upsert_page` rewrites the full property set on every matched
row, so the next `notion-sync run` reformats existing PR, activity, and
daily-steps pages in place.

## Error Handling

Formatters are total functions: `None`/≤0 durations yield `""`, unknown PR
types and unparseable values yield the raw string. No exception from
formatting can abort a sync.

## Testing

- `format_duration`: sub-hour, hour-and-up, fractional seconds, `None`, ≤ 0.
- `format_record_value`: one case per type family (duration, distance, ascent,
  power, steps, streak), unknown-type fallback, unparseable-value fallback.
- `format_record_pace` type 11 → `km/h`; types 1–6 unchanged.
- Mapper assertions: `activity_page` emits `Duration` text, `Distance (mi)`,
  min/mi pace, and keeps `Duration (min)`; `personal_record_page` emits the
  formatted `Value`; `daily_steps_page` emits `Total Distance (mi)`.

## Out of Scope

- Notion formula properties or API-side schema management.
- Any ingest, parser, or database schema change (Notion sync stays
  Postgres-only).
- PR units (stay metric) and activity power/calories (unit-neutral).
