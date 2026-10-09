# Issue tracker: Vikunja

Issues and specs for this repo are Vikunja tasks. Use the `vja-vikunja`
skill and `vja` for task operations. Find the repo's project with
`vja project ls` before creating tasks; ask which project to use if unclear.

Create tasks with `vja add` and a complete `--note`; read them with
`vja show <id> --json`; list them with `vja ls`; update labels with
`vja edit <id> -l <label>`; mark completed work done with
`vja edit <id> --done=true`. Use task IDs when referring to tickets.

For parent and blocking relationships, use the Vikunja relation
operations documented in the `vja-vikunja` skill. A ready ticket has
no incomplete blockers. Specs are parent tasks; implementation tickets
are child tasks with explicit blocking relations.

When a skill says to publish a spec or issue, create a Vikunja task.
When it says to fetch a ticket, read the referenced task and its
relations. Pull requests are not a triage request surface.

`vja` currently reports that its API URL must point to `/v2`;
repair that configuration before tracker operations.
