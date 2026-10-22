---
name: pipeline-brief
description: Pipedrive pipeline status brief — what moved, what is closing this week, what stalled, what needs attention, ranked by the team's own rules — for a rep or sales manager, on demand or scheduled. Use when the user asks for pipeline status, what to focus on today or this week, what is live, rotten or urgent, what happened or what they missed while away, a morning or Monday brief, a team pipeline summary, top customers this month, to prepare for a meeting that has not happened yet, confirms a discovered owner to continue a personal brief, asks for a visual dashboard or downloadable snapshot of an existing Pipeline Brief, or accepts or declines that dashboard. Before a meeting this skill prepares it; after a meeting, use meeting-follow-up. Do NOT use to diagnose stuck deals or create follow-up activities (use deal-triage), for a revenue forecast or target comparison (use forecast), or to log a meeting that already happened (use meeting-follow-up). Reads Pipedrive only; never writes.
allowed-tools: mcp__plugin_pipedrive-sales_pipedrive__getAccountContext mcp__plugin_pipedrive-sales_pipedrive__get_account_context mcp__plugin_pipedrive-sales_pipedrive__getDeals mcp__plugin_pipedrive-sales_pipedrive__getDeal mcp__plugin_pipedrive-sales_pipedrive__getActivities mcp__plugin_pipedrive-sales_pipedrive__getLeads mcp__plugin_pipedrive-sales_pipedrive__getNotes mcp__plugin_pipedrive-sales_pipedrive__getActivity mcp__plugin_pipedrive-sales_pipedrive__getPerson mcp__plugin_pipedrive-sales_pipedrive__getOrganization mcp__pipedrive__getAccountContext mcp__pipedrive__get_account_context mcp__pipedrive__getDeals mcp__pipedrive__getDeal mcp__pipedrive__getActivities mcp__pipedrive__getLeads mcp__pipedrive__getNotes mcp__pipedrive__getActivity mcp__pipedrive__getPerson mcp__pipedrive__getOrganization mcp__Pipedrive__getAccountContext mcp__Pipedrive__get_account_context mcp__Pipedrive__getDeals mcp__Pipedrive__getDeal mcp__Pipedrive__getActivities mcp__Pipedrive__getLeads mcp__Pipedrive__getNotes mcp__Pipedrive__getActivity mcp__Pipedrive__getPerson mcp__Pipedrive__getOrganization mcp__claude_ai_Pipedrive__getAccountContext mcp__claude_ai_Pipedrive__get_account_context mcp__claude_ai_Pipedrive__getDeals mcp__claude_ai_Pipedrive__getDeal mcp__claude_ai_Pipedrive__getActivities mcp__claude_ai_Pipedrive__getLeads mcp__claude_ai_Pipedrive__getNotes mcp__claude_ai_Pipedrive__getActivity mcp__claude_ai_Pipedrive__getPerson mcp__claude_ai_Pipedrive__getOrganization mcp__claude_ai_Pipedrive_MCP__getAccountContext mcp__claude_ai_Pipedrive_MCP__get_account_context mcp__claude_ai_Pipedrive_MCP__getDeals mcp__claude_ai_Pipedrive_MCP__getDeal mcp__claude_ai_Pipedrive_MCP__getActivities mcp__claude_ai_Pipedrive_MCP__getLeads mcp__claude_ai_Pipedrive_MCP__getNotes mcp__claude_ai_Pipedrive_MCP__getActivity mcp__claude_ai_Pipedrive_MCP__getPerson mcp__claude_ai_Pipedrive_MCP__getOrganization
compatibility: Requires the official Pipedrive MCP connector, which the plugin bundles as a reference: connect it from the plugin's Connectors tab, or sign in when Claude Code first calls it. Runs in Claude Cowork and Claude Code, where the plugin's hooks save responses and check replies; in claude.ai chat the hooks do not run, so say so and stop. scripts/compute.py needs code execution; without it the skill computes from its written rules and labels the result as not code-checked.
metadata:
  author: Pipedrive
  version: 0.7.22
  mcp-server: pipedrive
disallowed-tools: mcp__plugin_pipedrive-sales_pipedrive__addActivity mcp__plugin_pipedrive-sales_pipedrive__addDeal mcp__plugin_pipedrive-sales_pipedrive__addDealProduct mcp__plugin_pipedrive-sales_pipedrive__addLead mcp__plugin_pipedrive-sales_pipedrive__addNote mcp__plugin_pipedrive-sales_pipedrive__addOrganization mcp__plugin_pipedrive-sales_pipedrive__addPerson mcp__plugin_pipedrive-sales_pipedrive__addProduct mcp__plugin_pipedrive-sales_pipedrive__addProductVariation mcp__plugin_pipedrive-sales_pipedrive__addProject mcp__plugin_pipedrive-sales_pipedrive__convertLeadToDeal mcp__plugin_pipedrive-sales_pipedrive__updateActivity mcp__plugin_pipedrive-sales_pipedrive__updateDeal mcp__plugin_pipedrive-sales_pipedrive__updateDealProduct mcp__plugin_pipedrive-sales_pipedrive__updateLead mcp__plugin_pipedrive-sales_pipedrive__updateMailThreadDetails mcp__plugin_pipedrive-sales_pipedrive__updateNote mcp__plugin_pipedrive-sales_pipedrive__updateOrganization mcp__plugin_pipedrive-sales_pipedrive__updatePerson mcp__plugin_pipedrive-sales_pipedrive__updateProduct mcp__plugin_pipedrive-sales_pipedrive__updateProductVariation mcp__plugin_pipedrive-sales_pipedrive__updateProject mcp__pipedrive__addActivity mcp__pipedrive__addDeal mcp__pipedrive__addDealProduct mcp__pipedrive__addLead mcp__pipedrive__addNote mcp__pipedrive__addOrganization mcp__pipedrive__addPerson mcp__pipedrive__addProduct mcp__pipedrive__addProductVariation mcp__pipedrive__addProject mcp__pipedrive__convertLeadToDeal mcp__pipedrive__updateActivity mcp__pipedrive__updateDeal mcp__pipedrive__updateDealProduct mcp__pipedrive__updateLead mcp__pipedrive__updateMailThreadDetails mcp__pipedrive__updateNote mcp__pipedrive__updateOrganization mcp__pipedrive__updatePerson mcp__pipedrive__updateProduct mcp__pipedrive__updateProductVariation mcp__pipedrive__updateProject mcp__Pipedrive__addActivity mcp__Pipedrive__addDeal mcp__Pipedrive__addDealProduct mcp__Pipedrive__addLead mcp__Pipedrive__addNote mcp__Pipedrive__addOrganization mcp__Pipedrive__addPerson mcp__Pipedrive__addProduct mcp__Pipedrive__addProductVariation mcp__Pipedrive__addProject mcp__Pipedrive__convertLeadToDeal mcp__Pipedrive__updateActivity mcp__Pipedrive__updateDeal mcp__Pipedrive__updateDealProduct mcp__Pipedrive__updateLead mcp__Pipedrive__updateMailThreadDetails mcp__Pipedrive__updateNote mcp__Pipedrive__updateOrganization mcp__Pipedrive__updatePerson mcp__Pipedrive__updateProduct mcp__Pipedrive__updateProductVariation mcp__Pipedrive__updateProject mcp__claude_ai_Pipedrive__addActivity mcp__claude_ai_Pipedrive__addDeal mcp__claude_ai_Pipedrive__addDealProduct mcp__claude_ai_Pipedrive__addLead mcp__claude_ai_Pipedrive__addNote mcp__claude_ai_Pipedrive__addOrganization mcp__claude_ai_Pipedrive__addPerson mcp__claude_ai_Pipedrive__addProduct mcp__claude_ai_Pipedrive__addProductVariation mcp__claude_ai_Pipedrive__addProject mcp__claude_ai_Pipedrive__convertLeadToDeal mcp__claude_ai_Pipedrive__updateActivity mcp__claude_ai_Pipedrive__updateDeal mcp__claude_ai_Pipedrive__updateDealProduct mcp__claude_ai_Pipedrive__updateLead mcp__claude_ai_Pipedrive__updateMailThreadDetails mcp__claude_ai_Pipedrive__updateNote mcp__claude_ai_Pipedrive__updateOrganization mcp__claude_ai_Pipedrive__updatePerson mcp__claude_ai_Pipedrive__updateProduct mcp__claude_ai_Pipedrive__updateProductVariation mcp__claude_ai_Pipedrive__updateProject mcp__claude_ai_Pipedrive_MCP__addActivity mcp__claude_ai_Pipedrive_MCP__addDeal mcp__claude_ai_Pipedrive_MCP__addDealProduct mcp__claude_ai_Pipedrive_MCP__addLead mcp__claude_ai_Pipedrive_MCP__addNote mcp__claude_ai_Pipedrive_MCP__addOrganization mcp__claude_ai_Pipedrive_MCP__addPerson mcp__claude_ai_Pipedrive_MCP__addProduct mcp__claude_ai_Pipedrive_MCP__addProductVariation mcp__claude_ai_Pipedrive_MCP__addProject mcp__claude_ai_Pipedrive_MCP__convertLeadToDeal mcp__claude_ai_Pipedrive_MCP__updateActivity mcp__claude_ai_Pipedrive_MCP__updateDeal mcp__claude_ai_Pipedrive_MCP__updateDealProduct mcp__claude_ai_Pipedrive_MCP__updateLead mcp__claude_ai_Pipedrive_MCP__updateMailThreadDetails mcp__claude_ai_Pipedrive_MCP__updateNote mcp__claude_ai_Pipedrive_MCP__updateOrganization mcp__claude_ai_Pipedrive_MCP__updatePerson mcp__claude_ai_Pipedrive_MCP__updateProduct mcp__claude_ai_Pipedrive_MCP__updateProductVariation mcp__claude_ai_Pipedrive_MCP__updateProject
argument-hint: "[window] [scope]"
---

# Pipeline brief

Before any scope or owner question, check settings in the user's selected working
folder, then read `references/account-context-guide.md`. If verified current-session
metadata is missing, start the setup run first (`run_capture.py start …`, as that guide
says), then call the exposed context tool: in Cowork,
`mcp__Pipedrive__getAccountContext`. Use its actual pipeline names and calling user.
Do not build scope options from historical test logs, memory, or numeric IDs such as
“Pipeline 1”. Missing local setup is a discovery trigger. If the tool is unavailable
or fails, explain that and follow the documented fallback. Saved-output-only requests
reuse their verified report without new CRM calls.

## Critical

- Run the bundled scripts, never read them: each call is one plain `python3 <skill folder>/scripts/<script>.py …` command, nothing before `python3` and nothing after the arguments: no `cd`, `ls`, `&&`, `;`, `|`, `$(…)`, redirects, heredocs or `echo`. Some clients refuse anything else with a message that sounds like shell access is off for the whole session ("denied … don't ask mode"). It is per command: rerun the same script call in the plain form. A refusal never means the brief may be computed by hand; say which command was refused and retry it plainly. Only a command refused in the plain form starts "No script available".
- Read `references/company-url-guide.md` for the shared company URL setup. Before a new interactive report, ask for a missing company URL after scope/owner confirmation, with Skip for now. Reuse valid saved `output.domain` from any of the three skills without asking again; preserve it on settings updates. Scheduled runs and pending write confirmations never ask this question.
- For an unanswered interactive choice, read `references/question-guide.md` and use the current host's selectable question tool when exposed (`AskUserQuestion` or its documented equivalent), with a short numbered chat fallback. Respect typed answers; skipped or automatic answers are not consent. Do not ask again for a choice already given.
- Never call addActivity, addNote, updateActivity, updateDeal, updateLead or addDeal from this skill.
- Keep coverage diagnostics and the footer in the saved result; in the visible brief state the number checked and any unread boundary plainly. Never silently truncate; if the fetch was cut, say which kind of deal was left out.
- Every flagged line leads with a sales-rep explanation and a practical next step; keep rule IDs in saved diagnostics and use full plain-language descriptions beside the deal. A deal with no recent customer contact or no next activity is labelled "at risk" wherever it ranks. No raw JSON, no field keys, no record numbers (owner, deal, activity, note or filter IDs), including in your own notes after the computed report. Name owners and deals, refer to an activity by its subject, and name a saved filter or call it "your selected filter" with its link; never label anything by number ("filter 41", "activity 78") or quote an ID from a URL. Quote recorded dates and times as recorded; never attach a timezone to a time the report marks unverified.
- When compute.py has run, paste its Markdown **verbatim**, except that its exact trailing conversational question may be presented through `references/question-guide.md`. It starts with the number checked and the next actions. Do not replace it with a Bash result, tool card, or local-file preview.
- When compute.py reports partial coverage, keep the first-line warning and every computed limitation. Name at most five fetched deals. Do not imply full-pipeline totals, movement, or closing coverage.
- For a partial result, return the computed Markdown as the brief. Do not append free-form deal, movement, or coverage claims after it; those can accidentally turn a fetched-record finding into a whole-pipeline claim. Use the continue-or-filter decision from `references/question-guide.md` as the next question; deliver a clearly partial dashboard only if the user explicitly requests one.
- Never wrap computed Markdown in a code fence or append a correction to it. If a readable probe is followed by an unreadable or failed open-deals page, that is partial coverage even when the failed response has no cursor; paste the final computed partial block rather than reconstructing its totals by hand.
- When saved rule evidence marks R1 or R2 unavailable, return the computed limitation without manual advice, completion ticks, or an artifact offer. Those missing engagement inputs prohibit a priority recommendation and ranked actions.
- For an explicit overdue-work request, run `compute.py --mode overdue` against the saved run file after the bounded scan and paste that `Overdue work:` result. It is the only overdue activity list: never add closed-deal rows, unlinked calendar items, or separately calculated totals.
- After pasting an explicit overdue-work result, stop. Do not run another Pipeline Brief, compute a full brief, append coverage commentary, or make further Pipedrive calls.
- Never return an empty or apologetic brief; walk the ladder in references/output-format.md.
- Save every tool response to the run file (section 2) and let scripts/compute.py count calls and tokens. Do not count them yourself.
- Never call getActivities or getNotes with deal_id, never split a fetch per owner, and never re-fetch a large result in smaller pieces. If the client saved a tool result to a file instead of showing it, copy its original bytes with `scripts/response_capture.py` immediately and record the durable path as `response_file` — compute.py reads it from there.
- If the user declines a dashboard for an existing brief and asks to keep the text brief usable, read the saved result's `markdown` and paste it with its coverage line in the final response. Say plainly that the dashboard was declined for this conversation only, the preference was not saved, and future briefs may still offer it. Do not fetch Pipedrive or render an artifact for that decline.
- Never assume a setup answer, whether scope, owner, sales motion, timezone, filter, trials-or-demos choice or the confirmation to save. When saved settings are incomplete or an answer is missing, ask it with the selectable question tool (`references/question-guide.md`), one question at a time, and save only answers the user gave and confirmed. With complete saved settings go straight to the report.

## No script available

When the turn's tool list has no Bash (or no `python3`), or the first script command is still refused in the plain form (a refusal is per command: the first Critical rule), compute the brief in-model from the rules in this file, `references/rules.md` and "Computation definitions" in `references/legacy-procedure.md`. Open the reply with this line, exactly:

Not code-checked: this client could not run the skill's scripts, so the figures are the model's own reading of the rules.

Say why in one sentence (no Bash tool, no `python3`, or the refused command). Every output carries that label. State the number checked, scope, and any partial boundary plainly, in the same rep-first order as computed Markdown: start "I checked N open deals" with any partial-read warning, then give up to five named next actions and plain reasons, and write "arithmetic: model" in the coverage line. Add no calculation section, completion ticks or second status footer. Offer no dashboard, Claude Doc or visual artifact. Write no checkpoint, no history entry and no saved result: skip `run_state.py`, `settings.py` and the saved full result.

## Confirmed-profile workflow

When Python is available, retain the complete live responses and run the packaged
report engine before presenting totals, counts, ranking or outputs. Do not calculate
a substitute brief by hand or ask permission to finish required local capture and
computation. Never trim custom-field metadata from a response labelled complete.
A selected pipeline/owner must be reflected in the saved profile and supported
read selectors; inspect the actual connector schema rather than guessing its filters.
Use the computed coverage result, not a claim inferred from the final page alone.

For new setup, Quick/Advanced setup, familiar filters, today's meetings or version-2
settings, read `references/workflow-guide.md` and use packaged `workflow.py`. That flow
replaces the legacy setup/fetch/output sections below for this run. Optional rules stay
off; repeated typed filters and local scope checks produce a saved report with explicit
feature coverage. Valid account context resolves identity without name/email confirmation.
For a supplied saved run, use the guide's saved-run command recipe: resolve the working
folder once, save the report directly, then build and check the final reply. Keep data
files in that working folder and use the installed scripts by absolute path.
Legacy version-1 settings retain the sections below until reviewed migration. Never pass
raw version-2 settings to legacy compute. Meeting preparation and text skeletons are
read-only; saving notes to CRM requires an explicit Triage request.

Before a version-two report-only or delivery-only reply, use `workflow.py reply` and `check-reply` from the
workflow guide. Finish file checks and any evidence capture, then run
`check-reply --emit-markdown` as the last report-building Bash call. Send the text between its marker lines as the
entire final message, with no introduction, separator or extra file summary.
Evidence capture is a verification step: complete it before that final emission.
On show more, run `workflow.py page --input <the report you gave check-reply> --emit-markdown` once and send the text between its marker lines as your whole reply. The rep never sees what a command prints, so never answer "(above)" or with a pointer to it. One run per show more: never run it twice in a turn or page ahead to see what remains.
Do not add a receipt such as “Evidence captured” or “Sending the checked reply”. When the user asks something the brief does not answer, write `answer.json` and pass `--answer answer.json` to both `reply` and `check-reply` (format in the workflow guide); never move the brief into a file to make room for an explanation.
Brief mode retains the computed report; delivery mode
allows a short receipt. Use `schedule-plan` to build a complete proposal before explaining
unsupported scheduling. These local commands perform no CRM or host writes.

Setup checks, a requested brief's saved Markdown, `describe`, setup discovery, visual artifacts and the dating of saved evidence:
read `references/confirmed-profile.md` before that step. When Visual artifact is selected, also read
`references/visual-artifact-guide.md`.

Before recording MCP responses, read `references/response-capture.md`. Keep original
response files beside the run immediately, including when publishing later fails.
Never reconstruct raw rows from a summary or refetch solely to save a response.

## 0. Setup check

If the user asks only to save or read back confirmed settings and explicitly says not to run a brief, do not run discovery and do not call Pipedrive. Resolve the supplied choices, save and read back the complete config through `settings.py`, then stop.
Resolve settings at `./.pipedrive-sales/config.json` first. Use
`~/.pipedrive-sales/config.json` only when the project location cannot be read or written;
it is an alternate location, never a backup. Do not write that file directly from Python or the shell.
Verified saving (`save_verified`, `update_verified`, the `Settings saved:` and `Verified settings:` lines, the session-only fallback): read "Saving settings" in `references/legacy-procedure.md` before the first write.
No settings file, or a file with no `schema_version` and none of `sales_motion`, `scope`,
`silence_days`, `flag_no_next_activity` (for example only `tester_footer`), is a new setup:
run the version-2 setup in `references/workflow-guide.md` and never ask the questions in
`references/setup.md`. Only a saved version-1 file (some of those four keys, no `schema_version`)
with one of them null or
missing takes the legacy path: in interactive mode, do the first-run discovery in section 1 first, then read
`references/setup.md` and ask exactly one required question at a time (the rest stay off
until set). Do not persist scope or `last_run` until every required answer is confirmed
and the complete config has been verified. In
`mode=scheduled`, never ask: output one line
saying setup is required and stop. Do not read setup.md when config is complete.

When the user asks to "turn on the tester footer", set top-level `"tester_footer": true` in `./.pipedrive-sales/config.json`, keep every other key, read the file back and confirm in one line.

## 1. Resolve scope, identity and window

Before the first scope question of a legacy run, read "Pipelines, personal scope and owner names" in `references/legacy-procedure.md`.

- **Account discovery:** read the account-context tool once per chat session (a new chat and
every scheduled run read it fresh) per `references/account-context-guide.md`. Its authenticated
`current_user` replaces the notes identity probe; named pipelines replace discovery examples.
Save setup calls separately. Verified local scope/timezone/company settings win. No stage tool
exists: stage data comes only from this context read.
- **Scheduled mode probe:** the context read is the auth check. If it fails, output exactly
one line — "Pipedrive needs re-authorising; today's brief did not run." — and stop.
- **Pipelines first:** group the context's stages by `pipeline_id` and pass the saved context response to compute with `--stages <response file>`. If the context is unavailable or lacks stages for a confirmed pipeline, say stage data is unavailable and stop (`references/troubleshooting.md`). Show each pipeline's name and stage range. Stage names may make a pipeline *suspected* non-sales (for example "Case opened → Resolved"); a demo title or zero value never does. Name the suspected pipeline and ask before motion, owner or leads: `<pipeline name> (<first stage> → <last stage>) looks like a non-sales pipeline. Include all pipelines, or sales pipelines only?` No `scope.pipeline_ids`, `scope.exclude_pipeline_ids` or `last_run` is saved until the user answers. Put this sentence in the setup reply: "Until you answer, every discovered pipeline stays in scope; a demo title or zero value is not a reason to exclude one."
- **Personal scope and owner names:** for “my deals” with no saved owner, resolve identity from the context's `current_user` before an owner-scoped `getDeals` call. Never match records by position or fetch a team page under a “my deals” label while identity is unresolved. The fallback steps and the team owner-name question are in the reference.
- **Optional saved filter:** if `scope.filter_id` is set, fetch with it and skip owner and
pipeline filters (Pipedrive ignores them when filter_id is present).
- **Window:** default = since `config.last_runs["pipeline-brief"]` (legacy `last_run` only as a migration baseline), else 7 days; accept "since Friday",
"this week", "this month", "since I was away" (ask for the date once). Look-back for
activity and note signals is 90 days; if that yields fewer than ~5 human touches, re-run
once at 12 months and say which window was used.

## 2. Fetch (official MCP) — target 6–9 calls

Before the first read, read `references/fetch-contract.md` (run start, open-deal paging and the response-size policy; after a 429 its "429 recovery"). Start the run with `run_capture.py start` before the first read, so the hook's captures belong to it. Its `--run` takes the run file below as `./.pipedrive-sales/runs/pd-brief-<YYYY-MM-DD>.json`, with the leading `./`, and so does `absorb --run`.

Run file: `./.pipedrive-sales/runs/pd-brief-<YYYY-MM-DD>.json` (create the folder if
missing), shape `{"today": "<YYYY-MM-DD>", "window_start": "<YYYY-MM-DD>", "calls": []}`.
After **every** tool call append `{"tool": "<name>", "params": {...}, "response": <raw>}`
to `calls` — or, when the client wrote the result to a file because it was large,
`{"tool": "<name>", "params": {...}, "response_file": "<that path>"}`. compute.py reads only
this file. Copy the exact MCP request parameters into the run file at the moment of the call.
Keep connector arrays such as `"status":["open"]` and every raw deal row unchanged.
Complete inline connector JSON may be copied unchanged and checked using
`response_capture.py --inline-json` as described in `references/response-capture.md`.
An inaccessible transcript or missing wire-byte archive does not block that route.
If the response is incomplete or cannot be retained and checked accurately, state the
evidence limitation and stop; never reconstruct replacement rows from a summary.
Never add an owner, pipeline, filter, cursor, status, or limit later to make saved metadata
look more specific than the original request. Also save the measured wall time for that call
as `elapsed_ms`, from dispatch until its raw response arrives. Eight calls is the normal
total; do not add calls to work around large results.

1. Stages — already read in section 1 (context). Note per stage: `order_nr` (only when Pipedrive supplies
  it; without it compute skips the stage-order rules and says so), `deal_probability`,
  `is_deal_rot_enabled`, `days_to_rotten`.
2. **Open deals, oldest-updated first:** first call `getDeals(status="open", pipeline_id |
   filter_id, owner_id, limit=1, sort_by="update_time", sort_direction="asc",
   include_fields=[next_activity_id, last_activity_id, activities_count,
   undone_activities_count, email_messages_count, last_incoming_mail_time,
   last_outgoing_mail_time, participants_count, notes_count])`. With more than one pipeline in `scope.pipeline_ids`, send the probe and every ordinary open-deals page once per pipeline, each with that `pipeline_id`; never an unfiltered open-deals read. Save it, run compute.py
   `--pass 1`, and use its `suggested_page_size` (capped at 100 deals) for the first ordinary page, requested without `cursor`. Run pass 1 before **any** other read; on `integrity_stop` or unavailable stage data return that result and make no downstream reads. Fetch further pages with the same fields, their `cursor` copied from the next-page line `run_capture.py absorb` prints after each saved page (`references/fetch-contract.md`, never a cursor from memory), and recalculate after each. **If the last page you fetched still carries a `next_cursor` you did not follow, coverage is partial** — name the unread records by sort order and never present the fetched set as the whole pipeline.
   **Priority fields:** when config `priority_fields` is non-empty and the connector tool's own parameter list includes `custom_fields`, add `custom_fields=[<those field keys>]` (at most 15) to every open-deals call; when the list does not include it, send no such parameter. The fields show on each deal line; they do not change the ranking or the filtering.
3. **Movement:** two calls — `getDeals(status="won", updated_since=<window start at midnight in RFC3339>,
   limit=100)` and `getDeals(status="lost", updated_since=<same RFC3339 timestamp>, limit=100)`
   (for example `2026-09-10T00:00:00+02:00` in Berlin; a date alone is rejected).
   The server accepts one status per call. Compare `won_time` / `lost_time` /
   `stage_change_time` to the window. If step 2 was cut, fetch one more page of open
   deals `sort_direction="desc"` for "what moved" and "closing this week" only.
4. **Overdue work:** only on an explicit request, a bounded `getActivities(done=false, sort_by="due_date", sort_direction="asc", limit=<cap>)` scan, then `compute.py --mode overdue`. The scope rules (self, named owner, team) are in "Overdue work" in `references/legacy-procedure.md`.
5. **Leads** (if `sales_motion` is `speed_to_lead`/`hybrid`, or `scope.include_leads`):
   `getLeads(owner_id, sort="add_time", limit=100)` — the sort takes a bare field name
   and returns oldest first; while `more_items_in_collection` is true, page with `start`
   so the newest leads are included. compute.py orders them newest first. Untouched =
   `next_activity_id` is null and `was_seen` is false. Value is `value.amount` /
   `value.currency`. Archived leads are skipped. **Do not call getActivities per lead.**
6. **Rank pass 1:** run compute.py `--pass 1` (section 3). It returns `top_n_last_activity_ids`
   (≤ 30). Resolve their dates with one `getActivities(ids=[...], done=true)` per 100 ids
   (ids as strings; `done=true` because only completed activities count as a touch). Then
   run `--pass 2`.
7. **Notes, one call over the window** (only if `flag_notes_stage_mismatch` or
   `buying_signal_evidence` is on, or owner names are still unresolved):
   `getNotes(start_date=<look-back start>, sort="add_time DESC", limit=100)`; page with
   `start` while `more_items_in_collection`. compute.py joins notes to the deals that
   will be shown, strips HTML and drops system and duplicate notes. These rows also
   supply owner names (section 1). If `getNotes` is not in the tool list, make one tool search for it. If it
   is still missing, add `--unavailable getNotes` to the pass-2 command in section 3. The Markdown then carries
   this line; repeat it exactly as printed: `getNotes is not available in this workspace; ask your Pipedrive admin to re-add the connector. The notes-mismatch rule R7 and buying signals were skipped.` Never guess notes data; continue with the other checks.
The hook does not capture an error result; record a limit, 429 or error response in the run file yourself, so the script can name it. Retry any 5xx once; if it fails again, name the call in the coverage line. For any other
connector error, record that response and do not retry or reduce the page: only an
unexpectedly large or unreadable page uses the same-cursor retry above. Optional
enrichment is never a reason to abandon trustworthy core deal results. After a quota,
limit, or 429 response from `getActivities` or `getNotes`, record that response and finish
the final compute pass. It marks R1 unavailable after an activity limit and R7 plus
buying-signal evidence unavailable after a notes limit. State the optional-enrichment
limitation in the brief; never describe that tool's scan as complete.

## 3. Compute

Locate this skill's folder (the directory containing this SKILL.md) and **run**
`python3 <skill folder>/scripts/compute.py --mode brief --pass 1 --config <config path> --input ./.pipedrive-sales/runs/pd-brief-<date>.json --stages <saved context response>` after step 5, then the same command with
`--pass 2 --markdown --output ./.pipedrive-sales/results/pipeline-brief-full.json` after step 7 (add `--unavailable getNotes` there when step 7 found `getNotes` missing). When the user states an as-of date, pass it as `--today <YYYY-MM-DD>` to every script and use it for every "days since" and overdue statement. Do not read the script into context. Pass 1 prints a
short JSON summary (call count, token estimate, the activity ids to resolve); pass 2 with
`--markdown` prints the ready-to-paste brief, and `--output` makes the script write the complete result (every record's flags) to
`./.pipedrive-sales/results/pipeline-brief-full.json` itself: never redirect script output to a file yourself, and never run the
command bare for the paste. If the script exits with an error, it printed no brief: tell the user the brief was withheld because its reply check failed, name each failed check from stderr in plain words, and never write the brief yourself. Without code execution, follow "No script available". The flag, ranking and cluster definitions are in `references/rules.md`, and the formulas in "Computation definitions" in `references/legacy-procedure.md`.

After every fully fetched interactive brief, offer **Dashboard / Claude Doc / Visual artifact / Both / All three** through the selectable-question flow in `references/question-guide.md`, including Nothing else.
Read `references/coach-voice.md` when writing rep-facing prose. Keep its coaching grounded
in the computed actions and ask only one ownership question for the whole brief.
Read `references/presentation-guide.md` for native document creation, design choices,
branding, tested chart composition and delivery verification. Combined is the default;
explain its tradeoffs and the optional Claude-designed mode. Use the saved full pass-2
result, `./.pipedrive-sales/results/pipeline-brief-full.json`, which `--output` wrote; scheduled runs remain text-only. A decline keeps the existing Markdown available
and does not disable future offers. When the user declines, explicitly confirm that the
decline applies only to this conversation and was not saved as a future preference.

## 4–5. Aggregates and output shape

For "this week" answers, quote the window and count that compute.py prints ("Closing this week (12–18 Oct)"); never compute your own week or a 7-day range. Aggregates (open count and value by stage, movement, closing this week, concentration) and the three output shapes by record count (recall, triage, narrow or continue) are in "Aggregates" and "Output shape by record count" in `references/legacy-procedure.md`; the full templates are in `references/output-format.md`. Never return an empty brief.

Deep-link as `https://<config.output.domain>.pipedrive.com/deal/<id>` when the domain is
known, else "Deal name unavailable"; never invent a URL. Names not IDs. Never include raw JSON or
field keys. Do not write anything to Pipedrive. Full templates: references/output-format.md.

## 6. Scheduled mode

Lead with what changed since config.last_runs["pipeline-brief"] (legacy `last_run` only as a migration baseline) (moved, won, lost, newly at risk); fit a
phone screen (headline, 3–5 records); repeat caveats and hygiene lines only when new;
propose no writes — list them for the next interactive session. After a successful run,
complete the checkpoint procedure in `references/presentation-guide.md` and only claim it after read-back.

## 7. If the user corrects a result

Offer the smallest durable change — snooze this deal until a date (config.snoozed),
adjust a threshold, add a value floor or an exclusion, retune the ranking in their words
("staleness matters more") — write it to config on confirmation and say what changed.
Never just drop the line for this run.

## Before returning

Totals reconcile · every flagged line has a plain-language risk, next step, and supporting dates · at-risk labels applied ·
visible scan boundary and saved call count, with what was cut · saved footer with motion, weights, defaults ·
names not IDs · no Pipedrive writes · not empty. Advance only the Pipeline Brief checkpoint through `scripts/run_state.py` after verified setup, complete evidence and successful selected-output delivery. Preserve the previous checkpoint on failure.
If scope integrity fails, a required rule is unavailable, or coverage is partial, lead
with the computed limitation and its specific missing evidence. Do not add completion
ticks or a technical status footer to the visible brief.

## Troubleshooting

If tools are missing, a call fails, coverage is partial, a filter is not found or a quota
error appears, read `references/troubleshooting.md` and follow the matching row.

Evidence notes (unresolved activity dates, refreshing context names in a legacy run): "Evidence notes" in `references/legacy-procedure.md`.
