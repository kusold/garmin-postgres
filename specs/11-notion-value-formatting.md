# Notion Value Formatting

## Goal

Make the values synced to Notion readable by humans. Personal-record pages
currently show Garmin's raw numbers (`9876.0` for a marathon), and activity
durations show decimal minutes (`62.35`). Formatting happens in notion-sync
before pushing for personal-record values and a new duration text property.
Existing activity and daily-step measurements stay metric; the client handles
the presentation of those numeric columns and any unit conversion.

## Background

Notion number display formats do not express duration strings, unit suffixes,
or decimal-place rounding. Notion formulas can round values, but this spec
does not add formula properties.
Formula page values are read-only, though formula properties can be managed in
the data-source schema. This spec formats the existing personal-record text
property and adds one activity text property before pushing. The reference
project [kburgert/garmin-to-notion](https://github.com/kburgert/garmin-to-notion)
uses the same approach; its `format_garmin_value(value, type, typeId)` formats
known record types as durations, distances, power, steps, or streaks, with a
generic duration fallback. It excludes daily-streak records (type 16). This
spec explicitly covers types 5, 6, 11, and 16 as well.

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
| 7 | Longest Run | `32165.0` | `32.16 km` |
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

Already-formatted, nonnumeric duration values (for example `00:22:14`) remain
unchanged in `Value` and have a blank derived `Pace`. Parsing those values for
pace can be added if they occur in real archived records.

## Activities (metric)

`activity_page` changes:

- `Duration (min)` (number) stays for numeric sorting, and a new `Duration`
  text property carries `format_duration(seconds)` (`1:02:21`).

`Distance (km)` and `Avg Pace` keep their current metric values and formats.
`daily_steps_page` continues to write `Total Distance (km)` to the separate
daily-steps database. `Avg Pace` remains `min/km` even for cycling activities,
matching the reference project's activity formatter. Activity-specific pace or
speed presentation is deferred.

All other activity columns (calories, power in watts, training effect) are
unit-neutral or already readable and are untouched.

## Formatter Functions

- `format_duration(seconds)` — `m:ss` under an hour, `h:mm:ss` at or above;
  `""` for `None` or ≤ 0; uses Python's `round()` on fractional seconds first.
- `format_record_value(type_id, value_text)` — the PR table above. Reuses
  `format_duration` for types 1–6 and 11. Unknown `type_id` or unparseable
  value falls back to the raw string unchanged so data never disappears
  (mirroring `format_record_pace`'s degradation). Distance records divide meters
  by 1000, use Python's `round(km, 2)`, then display two decimal places; thus
  `32165.0` meters displays as `32.16 km` with binary floating-point rounding.
- `format_record_pace` gains a 40 km entry for type 11 and the `km/h` branch.
- `format_pace` keeps its current min/km output.

## One-Time Notion Setup (manual)

`NotionSink` has no schema-management role, so the activities database gets a
one-time UI edit:

- Add a text property named `Duration`.

The PR and daily-steps databases need no changes. `Value` and `Pace` in the
PR database are already text properties.

## Backfill

Personal records are replayed as a full snapshot, so the next sync reformats
their existing pages. Scheduled activity syncs use a two-day date window; to
populate `Duration` on older activity pages, run a one-time full-history sync
for each configured user without date limits, for example
`uv run notion-sync run --user <display-name> --data-type activities`.
`upsert_page` updates each matched page in place. Daily-steps pages need no
backfill because their values are unchanged.

## Error Handling

Formatters accept missing, invalid, and non-finite numeric inputs without
raising. `None`/≤0/non-finite durations yield `""`; unknown PR types and
unparseable or non-finite values preserve the raw string. Invalid values yield
a blank pace. A formatting error must not cause a row to be skipped.

## Testing

- `format_duration`: sub-hour, hour-and-up, fractional seconds, `None`, ≤ 0,
  non-finite and invalid inputs.
- `format_record_value`: one case per type family (duration, distance, ascent,
  power, steps, streak), unknown-type fallback, unparseable and non-finite
  value fallback, and the `32165.0`-meter rounding case.
- `format_record_pace` type 11 → `km/h`; types 1–6 unchanged; invalid and
  non-finite values yield blank pace; preformatted duration text preserves
  `Value` but yields blank pace.
- Mapper assertions: `activity_page` emits `Duration` text and keeps
  `Distance (km)`, min/km pace, and `Duration (min)`; `personal_record_page`
  emits the formatted `Value`; `daily_steps_page` keeps `Total Distance (km)`.

## Out of Scope

- Notion formula properties or API-side schema management.
- Any ingest, parser, or database schema change (Notion sync stays
  Postgres-only).
- Unit conversion in notion-sync. Activity distances and paces, daily-step
  distances, and PR units stay metric.
