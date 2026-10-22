# Account discovery with one setup call

The current Pipedrive context tool is `getAccountContext`, registered in Cowork as
`mcp__Pipedrive__getAccountContext`. Call it before any scope or owner question when
verified current-session metadata is missing. Missing local settings triggers this
discovery; it is not a reason to reconstruct options from a test log or memory. In Claude
Code or Codex, use the actual registered server namespace; the bundled plugin exposes
`mcp__plugin_pipedrive-sales_pipedrive__getAccountContext`. Older connectors may expose
`get_account_context`; use that alias only when it is actually registered and the
current tool is absent. Call one context tool, never both. Inspect its schema before calling it
and do not guess arguments.
If connector tools are loaded lazily, search/discover the current tool catalog for
`getAccountContext` and load it before deciding it is absent. Not yet loaded does not
mean unavailable. One successful context call replaces stage/example/identity probes.
Read the context once per chat session. A new chat and every scheduled run read it fresh;
later runs in the same chat reuse it. Nothing is saved across sessions and there is no refresh
rule. Report stages (names, `deal_probability`, rotting settings, and `order_nr` when Pipedrive
supplies it) come from this read; no skill uses a stage tool.
The context returns at most 500 custom fields per record type, with no paging.
`account_context.py` records the count per record type (`field_counts`) and sets
`may_be_incomplete` at 500. When the rep names a field you cannot see in the context, ask the
rep which field they mean; never treat it as empty or absent. When the flag is set for that
record type, say the field list may be incomplete.
The context call gets its own run, `./.pipedrive-sales/runs/<date>-setup.json` (if that file
already holds a call, use a new name such as `<date>-2-setup.json`); write the path that way, with the leading `./`, in `start --run` and `absorb --run`. Just before
calling the tool, start it: `python3 scripts/run_capture.py start --run <setup-run> --today <date> --settings ./.pipedrive-sales/config.json --skill <skill>`.
With a confirmed profile for that skill, the context call then returns a one-line receipt; the scripts read the
saved copy and `account_context.py` prints the names the reply needs. Setup without one shows the full response.
After the call, run `python3 scripts/run_capture.py absorb --run <setup-run> --context`. When it
prints `captured automatically: 1 response`, the plugin saved the response: use the printed
`context response:` file as the original response below and as `workflow.py --context`,
and do not copy it. Otherwise save
the complete response (including complete inline JSON) and exact request in that run,
following `response-capture.md`.
Use the normal run ledger (`calls` containing the observed tool, parameters and
retained response file); a top-level tool/response summary is not a replayable run.

Run `python3 scripts/account_context.py --input <original-response.json> --config ./.pipedrive-sales/config.json --run <retained-setup-run.json>` as a standalone command in the selected working folder.
Use the response file retained by that run. The helper reads the complete response and
saved metadata back, then emits a structured discovery receipt for local verification.
The receipt stays in tool output; the rep sees the calling name and named scope choices.
No additional test script is needed to verify this discovery step.
This accepts the observed `current_user`, `pipelines` with nested stages, `custom_fields`
and `company_users` shape. It returns normalized discovery data and saves pipeline names,
owner names, authenticated identity, and missing valid timezone/company domain. It
preserves confirmed scope, existing company URL/timezone, unknown settings and checkpoints.
This metadata-only save does not mean setup is complete. Never save selected scope until
its required answers are confirmed. Null locale/timezone fields remain unknown.
When handing discovery back to the user, state the calling user's verified name once
without asking them to confirm it, then ask for scope using the verified pipeline names.
For example: “You are signed in as Alex. Which pipelines should your deals cover?”
Discovery is complete; setup remains unfinished until the user accepts the required
choices. Do not say “Setup is done” at the discovery step. Keep capture receipts and
internal verification details out of the scope question.

For “my deals”, use `current_user.id`, not the first company user or a note author.
A company-users list without authenticated identity cannot resolve “me”. Use pipeline
names in selectable scope questions and report headers; keep numeric identifiers internal.
Disambiguate duplicate names with verified stage ranges or another recorded description.
If that is still ambiguous, ask for the selected view's URL rather than a numeric ID.
Keep stage names as supporting descriptions. Do not exclude Delivery or another pipeline
without confirmation. With verified saved scope, reuse it rather than asking again.
No discovery deal examples or notes identity probe are needed when context supplies
these fields. After a reset, repeat only this discovery call to rebuild setup choices.
Keep ordinary report stage/deal/activity evidence reads and their integrity checks.
Never merge setup calls or setup rows into the report's run to satisfy coverage.

Reuse a valid shared company domain; the authenticated `company_domain` supplies a
missing company URL without another question. Existing accepted settings win. Never
infer a company domain from email or use an account default currency to convert deals.
Use each deal's currency and keep currency totals separate. Carry verified timezone
into readable timestamps; preserve raw timestamps in evidence.

Field definitions describe available and optional response fields. They do not authorize
writes or expand a skill's whitelist. For new activities, `participants` with the
accepted deal contact as `primary: true` sets the activity contact; `person_id` itself
is read-only in this connector. Show that contact in the proposal before approval.

If the context tool is absent, fails, or its required shape is incomplete, explain the
missing metadata and use the skill's notes-based identity fallback where it has one. There
is no stage fallback: a report without stage data says so and stops.
Use verified stage descriptions or a saved-view URL when pipeline names are unavailable;
never fabricate “Pipeline 1” scope options from a prior log. A scheduled run never asks setup
questions; stop when required confirmed setup is missing.

## Connector errors, missing tools and tool names

- **403 on a read:** stop that read. Say once: "Pipedrive refused that request. Reconnect the Pipedrive connector in your client settings and try again." Never retry it and never substitute another tool for it. In deal-triage the same applies to a write: report it as not written and keep the item as a proposal only.
- **A tool is missing after a tool search:** say "<tool> is not available in this workspace; ask your Pipedrive admin to re-add the connector." Name the tool, stop that step, and never guess its data or use a different tool in its place. Then name the checks that depend on that tool and say they were skipped: without `getNotes`, the notes-mismatch rule R7 and buying signals; without `getActivities`, the silence and overdue rules.
- **The context tool:** call whichever name is registered (`getAccountContext` or the older `get_account_context`), never both. Before you say the context tool is unavailable, search for it by its exact name (`getAccountContext`), then by the connector names ("Pipedrive Beta", then "Pipedrive"), and retry the search once. Only then use the documented fallback.
