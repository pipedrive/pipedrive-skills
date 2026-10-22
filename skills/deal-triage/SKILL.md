---
name: deal-triage
description: Flags stalled, blocked or at-risk Pipedrive deals against the team's own rules, diagnoses the likely cause from activities and notes, and proposes one concrete next Activity per deal — written to Pipedrive only after the user confirms each one. Use when the user asks what is stuck, which deals are at risk, stalled, rotting or gone quiet, which need intervention, what to do about a specific deal, to flag revivable deals, or repeats "what's stuck?" after an accepted action. Do NOT use for a general pipeline status or "what should I focus on" overview (use pipeline-brief) or for revenue forecasts (use forecast). Writes only after an explicit, unambiguous accept.
allowed-tools: mcp__plugin_pipedrive-sales_pipedrive__getAccountContext mcp__plugin_pipedrive-sales_pipedrive__get_account_context mcp__plugin_pipedrive-sales_pipedrive__getDeals mcp__plugin_pipedrive-sales_pipedrive__getDeal mcp__plugin_pipedrive-sales_pipedrive__getActivities mcp__plugin_pipedrive-sales_pipedrive__getLeads mcp__plugin_pipedrive-sales_pipedrive__getNotes mcp__plugin_pipedrive-sales_pipedrive__getActivity mcp__plugin_pipedrive-sales_pipedrive__getPerson mcp__plugin_pipedrive-sales_pipedrive__getOrganization mcp__pipedrive__getAccountContext mcp__pipedrive__get_account_context mcp__pipedrive__getDeals mcp__pipedrive__getDeal mcp__pipedrive__getActivities mcp__pipedrive__getLeads mcp__pipedrive__getNotes mcp__pipedrive__getActivity mcp__pipedrive__getPerson mcp__pipedrive__getOrganization mcp__Pipedrive__getAccountContext mcp__Pipedrive__get_account_context mcp__Pipedrive__getDeals mcp__Pipedrive__getDeal mcp__Pipedrive__getActivities mcp__Pipedrive__getLeads mcp__Pipedrive__getNotes mcp__Pipedrive__getActivity mcp__Pipedrive__getPerson mcp__Pipedrive__getOrganization mcp__claude_ai_Pipedrive__getAccountContext mcp__claude_ai_Pipedrive__get_account_context mcp__claude_ai_Pipedrive__getDeals mcp__claude_ai_Pipedrive__getDeal mcp__claude_ai_Pipedrive__getActivities mcp__claude_ai_Pipedrive__getLeads mcp__claude_ai_Pipedrive__getNotes mcp__claude_ai_Pipedrive__getActivity mcp__claude_ai_Pipedrive__getPerson mcp__claude_ai_Pipedrive__getOrganization mcp__claude_ai_Pipedrive_MCP__getAccountContext mcp__claude_ai_Pipedrive_MCP__get_account_context mcp__claude_ai_Pipedrive_MCP__getDeals mcp__claude_ai_Pipedrive_MCP__getDeal mcp__claude_ai_Pipedrive_MCP__getActivities mcp__claude_ai_Pipedrive_MCP__getLeads mcp__claude_ai_Pipedrive_MCP__getNotes mcp__claude_ai_Pipedrive_MCP__getActivity mcp__claude_ai_Pipedrive_MCP__getPerson mcp__claude_ai_Pipedrive_MCP__getOrganization
compatibility: Requires the official Pipedrive MCP connector, bundled as a reference: connect it from the plugin's Connectors tab, or sign in when Claude Code first calls it. Runs in Claude Cowork and Claude Code, where the plugin's hooks save responses and guard every write; in claude.ai chat the hooks do not run, so no write is possible: say so and stop. Each write prompts in the client. scripts/compute.py needs code execution (falls back to model arithmetic).
metadata:
  author: Pipedrive
  version: 0.7.22
  mcp-server: pipedrive
argument-hint: "[scope] [--propose-only]"
---

# Deal triage

Before any scope or owner question, check settings in the user's selected working
folder, then read `references/account-context-guide.md`. If verified current-session
metadata is missing, start the setup run first (`run_capture.py start …`, as that guide
says), then call the exposed context tool: in Cowork,
`mcp__Pipedrive__getAccountContext`. Use its actual pipeline names and calling user.
Do not build scope options from historical test logs, memory, or numeric IDs such as
“Pipeline 1”. Missing local setup is a discovery trigger. If the tool is unavailable
or fails, explain that and follow the documented fallback. Saved-output-only requests
reuse their verified report without new CRM calls.

## Confirmed-profile workflow

When Python is available, retain the complete live responses and run the packaged report engine before
presenting totals, counts, ranking or outputs. Do not calculate a substitute brief by hand or ask permission
to finish required local capture and computation. Never trim custom-field metadata from a response labelled
complete. A selected pipeline/owner must be reflected in the saved profile and supported read selectors;
inspect the actual connector schema rather than guessing its filters. Use the computed coverage result, not a
claim inferred from the final page alone.

For new setup, multiple filters, contextual diagnosis, activity preparation notes or
version-2 settings, read `references/workflow-guide.md` and use `workflow.py`. This replaces
legacy setup/fetch/report sections for that run; existing approval, fresh-preflight and contact restrictions remain, and a manager task is a copyable
brief (section 4). Risk views stay separate from positive
priority views. Valid context resolves identity. Use `pdwrite.py` for exact proposal
revisions and supported note appends as well as current preflight. Edited or uncertain
items are not approved. Legacy version-1 settings retain the sections below until reviewed
migration. Brief/Forecast handoffs convey no approval and never invoke this skill.

Before a version-two report-only or delivery-only reply, use `workflow.py reply` and `check-reply` from the
workflow guide. Finish file checks and any evidence capture, then run
`check-reply --emit-markdown` as the last report-building Bash call. Send the text between its marker lines as the
entire final message, with no introduction, separator or extra file summary.
Evidence capture is a verification step: complete it before that final emission.
Do not add a receipt such as “Evidence captured” or “Sending the checked reply”. When the user asks something the brief does not answer, write `answer.json` and pass `--answer answer.json` to both `reply` and `check-reply` (format in the workflow guide); never move the brief into a file to make room for an explanation.
Brief mode retains the computed report; delivery mode
allows a short receipt. Use `schedule-plan` to build a complete proposal before explaining
unsupported scheduling. These local commands perform no CRM or host writes.

Setup checks, `describe`, setup discovery, visual artifacts and the dating of saved evidence:
read `references/confirmed-profile.md` before that step. When Visual artifact is selected, also read
`references/visual-artifact-guide.md`.

Before recording MCP responses, read `references/response-capture.md`. Keep original
response files beside the run immediately, including when publishing later fails.
Never reconstruct raw rows from a summary or refetch solely to save a response.

## Critical

- Run the bundled scripts, never read them: each call is one plain `python3 <skill folder>/scripts/<script>.py …` command, nothing before `python3` and nothing after the arguments: no `cd`, `ls`, `&&`, `;`, `|`, `$(…)`, redirects, heredocs or `echo`. Some clients refuse anything else with a message that sounds like shell access is off for the whole session ("denied … don't ask mode"). It is per command: rerun the same script call in the plain form. A refusal never means the preflight can be skipped, that a write may go ahead unguarded, or that the queue may be ranked by hand.
- Every write goes through a packaged preflight; never send a write tool parameters it did not return. With version-2 (confirmed-profile) settings that is `scripts/pdwrite.py`, including an item accepted in an earlier session: follow the write section of `references/workflow-guide.md`. The per-write output of `scripts/write_confirmation.py` is not the reply: after the last write of the turn, the reply is the output of `scripts/write_confirmation.py --turn` (section 5), sent unchanged as your whole reply. With version-1 settings it is `scripts/write_preflight.py`, which applies the shared whitelist and fresh-read rules in `scripts/write_rules.py`, and the same `scripts/write_confirmation.py` read-back (section 5); the rules below are the rules it enforces.
- Read `references/company-url-guide.md` for the shared company URL setup. Before a new interactive report, ask for a missing company URL after scope/owner confirmation, with Skip for now. Reuse valid saved `output.domain` from any of the three skills without asking again; preserve it on settings updates. Scheduled runs and pending write confirmations never ask this question.
- For an unanswered interactive choice, read `references/question-guide.md` and use the current host's selectable question tool when exposed (`AskUserQuestion` or its documented equivalent), with a short numbered chat fallback. Respect typed answers; skipped or automatic answers are not consent. Do not ask again for a choice already given.
- Read `references/coach-voice.md` for the Sales Coach tone on rep-facing decisions: state the supported risk, its CRM evidence, and one computed move. The existing accept/edit/skip confirmation is the ownership question. Do not infer buyer intent or change an activity subject without a separate confirmation.
- Nothing is written to Pipedrive before the user explicitly accepts that specific item in a later turn. If a bare "yes", "ok" or "go ahead" could answer more than one open question or proposal, do not assign it to any of them; ask which question or item the user meant.
- addActivity: `deal_id` or `lead_id`, `type`, `subject`, `due_date`, `due_time`, `note`, `participants` (the deal's own contact, primary); `user_id` only as the current user, on a deal the current user owns. Never `owner_id`, `outcome`, `priority`, `project_id`, `public_description`, `location`, `busy`, `attendees`, `org_id`, `duration`, `done`. addNote: `deal_id` or `lead_id`, `content`; never `user_id`, `add_time`, `project_id`, `task_id` or pin flags.
- updateActivity: `id` plus exactly one group: `due_date`/`due_time`, `done`, `participants` (the deal's own contact; the separately confirmed contact-link path in section 5), or `note` (only the reviewed append in `pdwrite.py`). Never `owner_id`, `subject`, `type`, deal/lead/org links, `outcome`, `priority`, `project_id`, `public_description`, `location`, `busy`, `attendees`.
- updateDeal: `id` plus exactly one of `status` (`won` only; never `lost`, `open` or `lost_reason`), `stage_id`, `expected_close_date`, `value`, `label_ids`; never anything else. status/stage changes need their own confirmation naming the change.
- Every other write tool is refused: never `addDeal`, `updateLead`, `updateOrganization`, `updatePerson`, `updateNote`, or any product, project or mail write tool.
- Never write in scheduled mode.
- Exclude Activities on won and lost deals from R8 and from the triage output, including cleanup findings.
- Every proposal has WHAT, HOW (Activity type), WHEN (due date), WHY (rule ids + dates).
- The `--pass 2 --markdown` output is checked before it prints: send the text between its marker lines exactly as printed, with the same headings (`Start with these decisions:`, `Complete action queue:`), the same line wording and the same `[accept N] [edit N] [skip N]` commands. Do not bold, retitle, reword, split into sub-bullets or reflow it; add your own sentences only before or after that block. When the command prints nothing and exits 1, the check refused the report: say so in one sentence and do not compose the queue by hand.
- Show every deal in `ranked_top10` with all of its rule flags, even when an existing Activity suppresses a new proposal. Paste the computed markdown lines instead of shortening them into a skipped-items summary.
- On **every** R6 deal line, repeat the saved filter's one-line `meaning` from config exactly as compute.py printed it. Do not shorten later matches to “R6 matches filter.”
- Do not append a guessed reason for unavailable evidence or reinterpret the computed queue after `--pass 2 --markdown`. If a connector response was unreadable, identify that response from the saved run; do not claim a CRM field is missing merely because a local file could not be read.
- Describe each confirmed write from its proposal's `action`: `addActivity` creates a new activity; `updateActivity` changes the linked existing activity's due date. Never summarize a mixed queue as if every acceptance creates a new call. An overdue linked activity takes precedence over a new follow-up proposal for the same deal.
- A dashboard is read-only presentation: it never calls a Pipedrive write tool. CRM confirmation and writes remain in chat only.

## No script available

When the turn's tool list has no Bash (or no `python3`), or the first script command is still refused in the plain form (a refusal is per command: the first Critical rule), triage proposes only and writes nothing: no preflight or read-back can run, so no write tool is called. Open the reply with this line, exactly:

Not code-checked: this client could not run the skill's scripts, so the figures are the model's own reading of the rules.

Say why in one sentence (no Bash tool, no `python3`, or the refused command). Print each proposal as one line, `Proposed: <Activity type> "<subject>", due <YYYY-MM-DD>`, followed by its reasons (rule ids and dates); the words `WHAT:` and `HOW:` are never printed as labels. Apply the section 2 rules in-model, keep each proposal's WHAT, HOW, WHEN and WHY, and write `arithmetic: model` in the coverage line. Then list what the user can do by hand in Pipedrive: create the proposed Activity on the deal, move an overdue Activity's due date, link the deal's contact.

## 0. Setup check
Read config. Required keys: everything `pipeline-brief` requires plus `single_thread_from_stage_id`,
`filter_rules` (may be empty), `writes_allowed`, `confirm_mode`. For missing keys read `references/setup.md`
and ask; otherwise do not read it. In scheduled mode: detect and propose only; never write; never ask. Use
`./.pipedrive-sales/config.json`, falling back to `~/.pipedrive-sales/config.json`. `filter_rules` contains
saved filter IDs, labels and meanings; never read a custom-field API key directly to implement R6. Confirm
each filter's scope and meaning before adding its numeric ID to config. Tester footer: when the user asks to turn it on, set top-level `"tester_footer": true` in `./.pipedrive-sales/config.json`, keep every other key, read it back and confirm in one line.

## 1. Fetch
If the user names specific deal IDs, keep that selection throughout both compute passes with `--deal-ids
<comma-separated-IDs>`. Fetch detail and propose actions only for those deals. The saved value floor does not
exclude a deal the user explicitly requested; the scan coverage may still describe all fetched deals. Do not
replace the selected review with the broad top-ten queue. An explicit deal selection excludes unrelated leads
and untouched-lead proposals, even when sales motion is `hybrid`.

Start the run with `run_capture.py start` before the first read, so the hook's captures belong to it. Its `--run` takes `./.pipedrive-sales/runs/pd-triage-<date>.json`, with the leading `./`, and so does `absorb --run`. Use the shared fetch contract in `references/fetch-contract.md` (open-deal paging, the response-size policy and 429 recovery): the account-context read (once per chat
session; fresh for each new chat and scheduled run) is the scheduled-mode probe and the only stage source; fetch open deals oldest-updated first in pages of 50, with the shared measured response-size and context-reserve policy; fetch won/lost, undone activities, optional leads, last-activity IDs and notes.
Save every tool response to `./.pipedrive-sales/runs/pd-triage-<date>.json` as
`{"today":"YYYY-MM-DD","window_start":"YYYY-MM-DD","calls":[{"tool":"getDeals","params":{},"response":{}}]}`;
use `response_file` instead of `response` when the client parks a large result on disk.
If a page cannot be saved/read, retry the same cursor once with a smaller page; then stop with partial coverage if it still fails. Never reread all deals in one oversized call to bypass this limit.
Preserve connector parameter arrays such as `"status":["open"]` exactly; do not edit
saved requests to satisfy the compute script.
Complete inline connector JSON may be copied unchanged and checked using
`response_capture.py --inline-json` in `references/response-capture.md`; missing original
wire bytes or inaccessible transcripts alone do not block the scan. Stop if the body is
incomplete or cannot be copied and checked accurately. Never reconstruct replacement
deal or activity rows from a summary, infer missing values or offer that as a workaround.
Do not fetch activities per deal during the broad scan. Then, **for each filter rule** in
config.filter_rules:
`getDeals(filter_id=<id>, status="open", limit=100)` — one call per rule; Pipedrive
evaluates the field logic and ignores `status` when `filter_id` is set (send it anyway so
every getDeals call carries it); follow its cursor if present. compute.py joins the
results to the open set by id (a filter missing `status = open` cannot pull in closed
deals this way). Then run compute.py `--pass 1` and `--pass 2` (section 2) to learn which deals
will be shown (`ranked_top10`), and **for those deals only** (max 10):
- `getDeal(id, include_fields=[…])` if a field is needed that the list did not return
- `getActivities(deal_id, limit=20, sort_by="update_time", sort_direction="desc")`
- `getNotes(deal_id, limit=10, sort="add_time DESC")` — compute.py strips HTML and drops system and duplicate
  notes. If `getNotes` is not in the tool list, make one tool search for it. If it is still missing, add
  `--unavailable getNotes` to the `--pass 2 --markdown` run below; the queue then carries this line, repeat it
  exactly as printed: `getNotes is not available in this workspace; ask your Pipedrive admin to re-add the connector. The notes-mismatch rule R7 and buying signals were skipped.` Never guess notes data; continue with the other checks.
Do not fetch detail for deals that will not be displayed.

## 2. Rules (thresholds from config; a null threshold disables its rule)
Locate this skill's folder (the directory containing this SKILL.md) and run
`python3 <skill folder>/scripts/compute.py --mode triage --pass 1 --config <config path>
--input <run file> --stages <saved context response>` (the plugin hook's capture file of the account-context read, `./.pipedrive-sales/runs/captured/<id>.json`, is accepted as it is) after the broad scan. When the user states an as-of date, pass it as `--today <YYYY-MM-DD>` to every script and use it for every "days since" and overdue statement. It prints `top_n_last_activity_ids`: resolve
them with one `getActivities(ids=[...], done=true)` per 100 ids, append the response, then
run `--pass 2` (compact JSON) — its `ranked_top10` names the deals to fetch detail for
(section 1). Append the detail responses and run `--pass 2 --markdown` (with `--unavailable getNotes` when the notes step found it missing): paste its decision
queue, coverage and limitations verbatim; retain detailed calculation evidence inside each decision card. Do not add a cause. Do not read the script into
context. Without code execution, follow "No script available" and print the same line
shapes with "arithmetic: model" in the coverage line. Full table with proposed Activity
per rule and the buying-signal phrase list in `references/rules.md`.
R1  SILENT         days_silent ≥ silence_days                                    → High
R2  NO_NEXT        complete open-activity evidence confirms absence             → High with R1, else Medium
R3  STAGE_STALE    days_in_stage ≥ days_to_rotten (if rotting on) or config      → Medium
R4  CLOSE_SLIPPED  expected_close_date < today − close_slip_slack_days            → Medium
R5  SINGLE_THREAD  verified participants_count ≤ 1 at configured stage          → Medium
R6  QUAL_GAP       returned by the "under-qualified" saved filter (first filter   → Medium
                   rule); the flag cites the user's own one-line meaning
R7  NOTES_MISMATCH a kept note says won/lost/signed/cancelled while status open   → High (data)
R8  OVERDUE        undone activities with due_date < today, on open deals only,   → Low–Medium
                   excluding activities with no deal_id/lead_id
R9  NO_VALUE       value null or 0 on an open deal beyond the first stage         → Low (data)
R10 FAR_CLOSE      probability ≥ high_probability_threshold && expected_close_date → Medium
                   > today + far_close_days — both thresholds off until set
R11 UNLINKED       open deal past the first stage with org_id or person_id null   → Low (data)
F-* FILTER_RULE    returned by any further saved filter in config.filter_rules    → per rule
Rank: severity, then priority. Output one ordered list, max 10 deals. Every ranked deal has an item number (its place in the queue) and, unless a task is due within 3 days, a proposal: the list shows the first page, the saved result holds the rest, each show-more page prints its own items, and an accept by number works for any page (0.7.15). Import clusters and
integration shells (from compute.py) are one line each, never ranked as deals. A pipeline with no Pipedrive `order_nr` skips R5, R9, R11, stage depth and the first-stage rule, with one line per pipeline: "Stage order not available for <pipeline name>; stage-order rules skipped."
R9 proposes a Task "add deal value" (or updateDeal(value) if a note states the amount —
separate confirmation). R10 and R11 proposals and their reasons: `references/rules.md`.

After a completed **interactive** text triage, the complete pass-2 JSON result is saved once at
`./.pipedrive-sales/results/deal-triage-full.json` by adding `--output ./.pipedrive-sales/results/deal-triage-full.json` to the pass-2 `--markdown` command:
the script writes it and still prints the Markdown; never redirect script output to a file yourself. This is the only renderer input.
Do not fetch Pipedrive again, recompute, or alter this saved result for a dashboard request. Do not save or offer a dashboard in scheduled mode.

After every completed interactive triage, offer **Dashboard / Claude Doc / Visual artifact / Both / All three**.
Read `references/presentation-guide.md` for native document creation, design choices,
branding, tested chart composition and delivery verification. Combined is the default;
explain its tradeoffs and the optional Claude-designed mode. Rendering the latest saved
result never accepts an item or writes to CRM. Preserve original scan dates and proposals;
update execution status from matching saved successful write responses before output.

## 3. Explain the queue
Paste the computed Markdown verbatim. It leads with the top three decisions and retains
the complete queue and per-item evidence, without a global calculation section. Do not add a guessed cause or
duplicate evidence. If a task already exists, show that task and let the rep review its
wording in Pipedrive; acceptance never changes an activity subject.
Show a buying-signal chip when a note contains buyer-side commercial language (budget
confirmed, quote accepted, legal or procurement looped in, start date named) — a deal
with a buying signal and no next step is at risk, not hot; say so. Distinguish a quiet
deal (nobody engaged — chase) from an adoption gap (one person engaged, the wider account
not — a company-level conversation) when participants or notes show which it is.
If nothing in the record supports a cause, say "no cause visible in Pipedrive" — do not guess.

## 4. Propose (one supported decision per deal)
Keep each computed proposal's exact subject, activity type, due date, and evidence.
The first screen shows one decision per deal in plain language. Rule IDs and thresholds
belong in saved diagnostics.
Skip proposing for a deal that already has an undone activity due within 3 days
(idempotency); keep its ranked deal line, its item number and every rule flag, then show the existing
Activity in place of a new proposal. Never drop a filter match from the displayed
findings just because it needs no new Activity.
Use `references/output-format.md` for the exact proposal and run-log shape.
Triage never drafts email text. An Email-type proposal ends with exactly one line, `Say "draft the email for <n>" to get the text.` (`<n>` is its item number; `compute.py` prints it). `sales-outreach` writes the email.

For cross-owner manager triage, never create a task, call, meeting or email on a rep's behalf: no cross-owner
write exists on either path. Show a copyable, owner-addressed task brief instead (type `task`, an
internal/coaching subject and note that say so, the deal owner, due date and linked record) for the
manager to create in Pipedrive or paste to the owner. The buyer-facing proposal stays evidence for review
and is never sent as the manager's task. An unassigned record gets a brief with no owner named. The
dashboard shows the same brief and creates nothing; chat confirmation, write permissions and the audit log still apply to every other item.

## 5. Confirm and write
Use `references/question-guide.md` for a selectable Accept item N / Edit item N / Skip item N decision when the host supports it. The question's text is script output: run `python3 <skill folder>/scripts/write_confirmation.py --ask <n> --result ./.pipedrive-sales/results/deal-triage-full.json` and put the text between its marker lines in the question (or in chat); it names the item, deal, type, subject and due date from the saved result. Only its explicit user answer can accept that item. `--ask <n>` is for an item the rep has not yet accepted; on an accept turn, run the preflight for each accepted item and then the turn command, and nothing else: no `--ask` for an item already accepted, and no `--help` on any script (a script's usage is in this file, never in `--help`). The question interface never replaces the fresh-read and separate-confirmation rules below.
**Every write is one of this report's proposals.** With no items, there is nothing to accept; a change the rep asks for is a new scan with the optional checks on, never a hand-written write. `edit <n>` changes only the item's due date: run `scripts/write_confirmation.py --edit <n> --due-date <YYYY-MM-DD> --result ./.pipedrive-sales/results/deal-triage-full.json`, which changes the saved item and prints its question again; a subject or type change is a skip or a new scan. The preflights refuse an item the saved result does not propose, and a proposal whose deal, activity or fields differ from it. On a report with no items, or a request the report did not propose, the reply is the output of `scripts/write_confirmation.py --ask <n>` for the item the rep named (1 when the report has none): it prints the refusal, and no write is offered outside this skill. The plugin's write guard stops a Pipedrive write no preflight printed (its reason starts with `WRITE-GUARD`): never retry or reword such a call; run the turn command (section 5) or `--ask <n>`, whose output names the stopped write.
Two confirmation modes (config.confirm_mode):
- per_item (default for a team's first runs): each proposal shown with [accept] [edit] [skip].
- batch: show the full numbered list once, then ask for one reply such as
  "accept 1, 3, 5", "accept all", "accept all except 2", or "skip all".
Offer batch mode after the second run; inexperienced users tend to click accept
repeatedly without reading when prompted many times, so one clear review beats ten
reflexive approvals. In both modes the confirmation text names the exact Activity that
will be created — type, subject, due date, deal — in plain words. Never "apply changes?".
Dashboard review can only prepare a named item for chat; before any write, chat restates
the item number, deal, type, subject, due date, and recipient and waits for `accept <item>`.
**Ambiguous replies are not answers or accepts.** If the user's message is a bare
"yes", "ok", "do it" or "go ahead" and more than one question or proposal is open,
do not infer which one it answers and do not write. Ask "Which question or item did
you mean?" and wait. An accept names the item ("accept 1 and 3", "yes, the TechFlow
call"). A misread that marks live deals lost is the failure this design prevents.
Only after an explicit, unambiguous accept:
- An item from an earlier chat is written only when its saved result is still on disk; without it, run triage
  again and accept from the new report. A proposal rebuilt from the conversation is never written.
- Re-read the deal (or lead) and its open activities immediately before an activity write.
  Save the complete raw responses. If the item is already written, report its Activity ID
  and do not repeat it. If the deal is closed, an equivalent step now exists, or the target
  activity changed, stop that item and explain the current state. Across a date rollover,
  retain the original scan date, check the current due date, and reconfirm any changed
  proposal. A failed read blocks that item; it does not justify using cached evidence.
- For a deal-linked activity, run `scripts/write_preflight.py --today <current-date>
  --result ./.pipedrive-sales/results/deal-triage-full.json --item <n>` after the fresh reads, with nothing else: the saved
  item is the proposal, the fresh reads are the plugin hook's captures of this turn's re-reads, and the deal's contact comes
  from that fresh read. Write no proposal file and no copy of a response (0.7.14: a copied item with a null contact was refused
  as a changed contact). Only an edited proposal takes `--proposal <file>`; only a host without plugin hooks takes
  `--deal-response <fresh-deal.json> --activities-response <fresh-complete-activities.json>`. Submit only its returned activity params; a refusal stops this item's
  write, never the item: it stays in `--accepted`, and the turn command (section 5) reports what was not written.
  Preserve the read timestamps and confirm they are from this acceptance turn. It never fetches or writes;
  lead proposals keep the separate fresh-lead check above.
- `addActivity(deal_id | lead_id, type, subject, due_date, participants, note=WHY)`. For a new deal activity, include the deal’s recorded contact ID in the proposal before approval. Use the accepted contact returned by preflight; if it changes, reconfirm the proposal. Do not change the contact when merely rescheduling an existing activity.
- R7 fixes require a second, separate confirmation naming the change before `updateDeal(id, status="won"|"lost")`;
  the turn command (section 5) prints that question when the item is accepted: never write it yourself. On the first yes an
  R7 item is neither preflighted nor written; the preflight and the write follow the second confirmation, and a
  refused verify task never removes the status question.
- Before `updateDeal`, re-read the deal and compare its current status and stage
  with the accepted proposal. If the read fails or either field changed, stop that
  item and ask the user to review the current deal. Do not offer a way to skip this check.
- R8 reschedules use `updateActivity(id, due_date)` after accept.
- After every write, read the record back by the id the write returned (`getActivity` for an activity,
  `getDeal` for a deal, `getNotes` for the deal's notes), save that response (append it to `calls` with `"purpose":"write_read_back"`), and run
  `scripts/write_confirmation.py --proposal <preflight output with deal_id added> --read-back <saved read-back>
  --fresh <run file> --config <config path> --result ./.pipedrive-sales/results/deal-triage-full.json --item <n>`, where `<n>` is the accepted item number.
  The command records the item's state in that saved result and its confirmation in the turn ledger. Its per-item output is not the reply. Report the item as written only when
  the read-back matches the accepted fields; otherwise it says "uncertain … do not resubmit". If it exits 3, the state was not saved; the turn reply below still shows the item from the ledger. Never retry the write.
- To add a contact to an existing Activity, show a separate proposal naming the deal, Activity subject and the
  deal's linked contact; acceptance of a reschedule or another triage item does not include it. After acceptance,
  read `references/contact-link.md` and run `scripts/contact_link.py`; use only its exact `params` with
  `updateActivity`: `id` and `participants:[{"person_id":<deal-person-id>,"primary":true}]`, never another person
  or Activity field. A failed preflight stops the item. Re-read the Activity after the write.
- R4/R9 fixes use `updateDeal(id, expected_close_date | value)` after a separate confirmation.
updateDeal never carries any parameter beyond id and the one field being fixed.
Never call addActivity or addNote without a deal_id (or lead_id): an activity created
without its record is invisible on the deal and lost to the audit trail.
Duplicates, junk records and merge/delete needs are reported as findings for the user
to handle in Pipedrive — never proposed as writes (there is no merge tool, and the
delete parameter on updateDeal is forbidden here).
If config.writes_allowed is false, or a write is refused by permissions: propose only
and say so. A 403 on the write: that item is not written, the rest of the run is propose-only, say once
"Pipedrive refused that request. Reconnect the Pipedrive connector in your client settings and try again.",
and never retry it: run `scripts/write_confirmation.py --refused --item <n>` for that item, then the turn command
(section 5), whose output carries that line and the not-sent items. In scheduled mode: list proposals for the next interactive session.
Send accepted writes one at a time, each in its own message: make the next `addActivity`, `addNote` or `updateActivity` call only after the previous write's response is back, and its read-back is done when it succeeded. Never put two write calls in one message. After a 403, do not send the remaining accepted items; list them as proposals only.
Bookkeeping on every confirmation turn: record the item numbers the user accepted and
skipped in the run file as `accepted_items` and `skipped_items`, and append each write
call with its raw response to `calls` (a refused write is appended too, with its error).
Update the original full result with `scripts/triage_execution.py` through the documented
refresh command in `references/presentation-guide.md`; do not rerank or renumber accepted
items from stale scan data. Retain the original scan date.
After the last write of the turn (or at once, when no write is sent), run as the last Bash call:
`python3 <skill folder>/scripts/write_confirmation.py --turn --accepted <n,…> --skipped <n,…> --result ./.pipedrive-sales/results/deal-triage-full.json [--run <run file>]` (`--run` is optional: without it, or with a path that is not a file, the newest triage run file in the runs folder is used; `--skipped` may be given with no numbers)
with every accepted item in `--accepted`, a status change still waiting for its second yes included (the output asks it, also when a preflight refused that item's activity). Send the text between its marker lines as the whole reply: no introduction, no note, no question after it, no "Checked" line.
For a new scan, fetch fresh evidence and give it a new dated result.

## 6. Run log
On a proposal or report turn, compute.py's last line is the run log: `Proposed N · accepted A · skipped S · written W · K tool calls`; it counts W from successful writes only, so paste it verbatim. A confirmation turn's run log is the `--turn` output (section 5). Persist: set
`config.triage_stats["<today>"]` to the cumulative counts across distinct saved scans for that day, replacing the prior cumulative value rather than adding it again on each confirmation. Retain dated evidence and advance only the Deal Triage checkpoint through the completion procedure in `references/presentation-guide.md`; never write the shared legacy `last_run`.
Under speed_to_lead / hybrid, also list untouched leads older than silence_days as
"leads going cold" with a proposed first-contact Call — same confirm rules apply.

## 7. If the user corrects a flag
When the user asks "what's stuck?" or requests triage again after an accepted write,
fetch current Pipedrive data and run triage again. Do not answer only from the prior
proposal list. Check existing undone Activities before proposing another action.
"Not stalled — on holiday until the 30th" → offer to snooze the deal until that date.
"We never chase under €5k" → offer a value floor. Write to config on confirmation; say
what changed. Do not silently drop the item for this run only.

## Before returning (every turn)
Shared checklist (section 2.6.4) plus: no write tool was called in this turn unless the user
unambiguously accepted that item in an earlier turn; every written activity carries
deal_id/lead_id; no updateDeal call carried more than id plus one whitelisted field;
run log present (proposed / accepted / skipped / written / tool calls). Compare every R6 line in the final answer with compute.py's corresponding numbered line; if the saved filter meaning was shortened or omitted, restore the full computed clause. Return with: "Checked: rules ✓ proposals complete ✓ writes-only-after-accept ✓ whitelist ✓ log ✓" on proposal and report turns only; a confirmation turn returns the `--turn` output alone.

## Troubleshooting
See `references/troubleshooting.md`. Triage-specific: if a write is refused by
permissions, switch to propose-only for the rest of the run and say which permission
is missing.