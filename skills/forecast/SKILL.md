---
name: forecast
description: Builds a read-only Pipedrive revenue forecast for a selected period, with probability source, cautious adjustments, targets, coverage, risks, and a leadership-ready explanation. Use when the user asks for a forecast, projected revenue, whether the team is on track, commit versus best case, coverage against target, period performance, or a visual dashboard of an existing Forecast. Do NOT use for daily priorities or general pipeline status (use pipeline-brief), or to diagnose and fix stuck deals (use deal-triage). Reads Pipedrive only; never writes.
allowed-tools: mcp__plugin_pipedrive-sales_pipedrive__getAccountContext mcp__plugin_pipedrive-sales_pipedrive__get_account_context mcp__plugin_pipedrive-sales_pipedrive__getDeals mcp__plugin_pipedrive-sales_pipedrive__getActivities mcp__plugin_pipedrive-sales_pipedrive__getNotes mcp__plugin_pipedrive-sales_pipedrive__getDeal mcp__plugin_pipedrive-sales_pipedrive__getActivity mcp__plugin_pipedrive-sales_pipedrive__getPerson mcp__plugin_pipedrive-sales_pipedrive__getOrganization mcp__pipedrive__getAccountContext mcp__pipedrive__get_account_context mcp__pipedrive__getDeals mcp__pipedrive__getActivities mcp__pipedrive__getNotes mcp__pipedrive__getDeal mcp__pipedrive__getActivity mcp__pipedrive__getPerson mcp__pipedrive__getOrganization mcp__Pipedrive__getAccountContext mcp__Pipedrive__get_account_context mcp__Pipedrive__getDeals mcp__Pipedrive__getActivities mcp__Pipedrive__getNotes mcp__Pipedrive__getDeal mcp__Pipedrive__getActivity mcp__Pipedrive__getPerson mcp__Pipedrive__getOrganization mcp__claude_ai_Pipedrive__getAccountContext mcp__claude_ai_Pipedrive__get_account_context mcp__claude_ai_Pipedrive__getDeals mcp__claude_ai_Pipedrive__getActivities mcp__claude_ai_Pipedrive__getNotes mcp__claude_ai_Pipedrive__getDeal mcp__claude_ai_Pipedrive__getActivity mcp__claude_ai_Pipedrive__getPerson mcp__claude_ai_Pipedrive__getOrganization mcp__claude_ai_Pipedrive_MCP__getAccountContext mcp__claude_ai_Pipedrive_MCP__get_account_context mcp__claude_ai_Pipedrive_MCP__getDeals mcp__claude_ai_Pipedrive_MCP__getActivities mcp__claude_ai_Pipedrive_MCP__getNotes mcp__claude_ai_Pipedrive_MCP__getDeal mcp__claude_ai_Pipedrive_MCP__getActivity mcp__claude_ai_Pipedrive_MCP__getPerson mcp__claude_ai_Pipedrive_MCP__getOrganization
compatibility: Requires the official Pipedrive MCP connector, bundled as a reference: connect it from the plugin's Connectors tab, or sign in when Claude Code first calls it. Runs in Claude Cowork and Claude Code, where the plugin's hooks save responses and check replies; in claude.ai chat the hooks do not run, so say so and stop. Targets are local: the MCP has no Goals read tool. scripts/compute.py needs code execution; without it the skill computes from its rules and labels the result as not code-checked.
metadata:
  author: Pipedrive
  version: 0.7.22
  mcp-server: pipedrive
disallowed-tools: mcp__plugin_pipedrive-sales_pipedrive__addActivity mcp__plugin_pipedrive-sales_pipedrive__addDeal mcp__plugin_pipedrive-sales_pipedrive__addDealProduct mcp__plugin_pipedrive-sales_pipedrive__addLead mcp__plugin_pipedrive-sales_pipedrive__addNote mcp__plugin_pipedrive-sales_pipedrive__addOrganization mcp__plugin_pipedrive-sales_pipedrive__addPerson mcp__plugin_pipedrive-sales_pipedrive__addProduct mcp__plugin_pipedrive-sales_pipedrive__addProductVariation mcp__plugin_pipedrive-sales_pipedrive__addProject mcp__plugin_pipedrive-sales_pipedrive__convertLeadToDeal mcp__plugin_pipedrive-sales_pipedrive__updateActivity mcp__plugin_pipedrive-sales_pipedrive__updateDeal mcp__plugin_pipedrive-sales_pipedrive__updateDealProduct mcp__plugin_pipedrive-sales_pipedrive__updateLead mcp__plugin_pipedrive-sales_pipedrive__updateMailThreadDetails mcp__plugin_pipedrive-sales_pipedrive__updateNote mcp__plugin_pipedrive-sales_pipedrive__updateOrganization mcp__plugin_pipedrive-sales_pipedrive__updatePerson mcp__plugin_pipedrive-sales_pipedrive__updateProduct mcp__plugin_pipedrive-sales_pipedrive__updateProductVariation mcp__plugin_pipedrive-sales_pipedrive__updateProject mcp__pipedrive__addActivity mcp__pipedrive__addDeal mcp__pipedrive__addDealProduct mcp__pipedrive__addLead mcp__pipedrive__addNote mcp__pipedrive__addOrganization mcp__pipedrive__addPerson mcp__pipedrive__addProduct mcp__pipedrive__addProductVariation mcp__pipedrive__addProject mcp__pipedrive__convertLeadToDeal mcp__pipedrive__updateActivity mcp__pipedrive__updateDeal mcp__pipedrive__updateDealProduct mcp__pipedrive__updateLead mcp__pipedrive__updateMailThreadDetails mcp__pipedrive__updateNote mcp__pipedrive__updateOrganization mcp__pipedrive__updatePerson mcp__pipedrive__updateProduct mcp__pipedrive__updateProductVariation mcp__pipedrive__updateProject mcp__Pipedrive__addActivity mcp__Pipedrive__addDeal mcp__Pipedrive__addDealProduct mcp__Pipedrive__addLead mcp__Pipedrive__addNote mcp__Pipedrive__addOrganization mcp__Pipedrive__addPerson mcp__Pipedrive__addProduct mcp__Pipedrive__addProductVariation mcp__Pipedrive__addProject mcp__Pipedrive__convertLeadToDeal mcp__Pipedrive__updateActivity mcp__Pipedrive__updateDeal mcp__Pipedrive__updateDealProduct mcp__Pipedrive__updateLead mcp__Pipedrive__updateMailThreadDetails mcp__Pipedrive__updateNote mcp__Pipedrive__updateOrganization mcp__Pipedrive__updatePerson mcp__Pipedrive__updateProduct mcp__Pipedrive__updateProductVariation mcp__Pipedrive__updateProject mcp__claude_ai_Pipedrive__addActivity mcp__claude_ai_Pipedrive__addDeal mcp__claude_ai_Pipedrive__addDealProduct mcp__claude_ai_Pipedrive__addLead mcp__claude_ai_Pipedrive__addNote mcp__claude_ai_Pipedrive__addOrganization mcp__claude_ai_Pipedrive__addPerson mcp__claude_ai_Pipedrive__addProduct mcp__claude_ai_Pipedrive__addProductVariation mcp__claude_ai_Pipedrive__addProject mcp__claude_ai_Pipedrive__convertLeadToDeal mcp__claude_ai_Pipedrive__updateActivity mcp__claude_ai_Pipedrive__updateDeal mcp__claude_ai_Pipedrive__updateDealProduct mcp__claude_ai_Pipedrive__updateLead mcp__claude_ai_Pipedrive__updateMailThreadDetails mcp__claude_ai_Pipedrive__updateNote mcp__claude_ai_Pipedrive__updateOrganization mcp__claude_ai_Pipedrive__updatePerson mcp__claude_ai_Pipedrive__updateProduct mcp__claude_ai_Pipedrive__updateProductVariation mcp__claude_ai_Pipedrive__updateProject mcp__claude_ai_Pipedrive_MCP__addActivity mcp__claude_ai_Pipedrive_MCP__addDeal mcp__claude_ai_Pipedrive_MCP__addDealProduct mcp__claude_ai_Pipedrive_MCP__addLead mcp__claude_ai_Pipedrive_MCP__addNote mcp__claude_ai_Pipedrive_MCP__addOrganization mcp__claude_ai_Pipedrive_MCP__addPerson mcp__claude_ai_Pipedrive_MCP__addProduct mcp__claude_ai_Pipedrive_MCP__addProductVariation mcp__claude_ai_Pipedrive_MCP__addProject mcp__claude_ai_Pipedrive_MCP__convertLeadToDeal mcp__claude_ai_Pipedrive_MCP__updateActivity mcp__claude_ai_Pipedrive_MCP__updateDeal mcp__claude_ai_Pipedrive_MCP__updateDealProduct mcp__claude_ai_Pipedrive_MCP__updateLead mcp__claude_ai_Pipedrive_MCP__updateMailThreadDetails mcp__claude_ai_Pipedrive_MCP__updateNote mcp__claude_ai_Pipedrive_MCP__updateOrganization mcp__claude_ai_Pipedrive_MCP__updatePerson mcp__claude_ai_Pipedrive_MCP__updateProduct mcp__claude_ai_Pipedrive_MCP__updateProductVariation mcp__claude_ai_Pipedrive_MCP__updateProject
argument-hint: "[period] [scope]"
---

# Forecast

Before any scope or owner question, check settings in the user's selected working
folder, then read `references/account-context-guide.md`. If verified current-session
metadata is missing, start the setup run first (`run_capture.py start …`, as that guide
says), then call the exposed context tool: in Cowork,
`mcp__Pipedrive__getAccountContext`. Use its actual pipeline names and calling user.
Do not build scope options from historical test logs, memory, or numeric IDs such as
“Pipeline 1”. Missing local setup is a discovery trigger: in a workspace with no saved
forecast profile, the first question after the context read is the `workflow.py setup-check`
card (Complete setup, the default, runs the quick setup and saves it); `references/legacy-procedure.md` serves settings
saved before version 2 only, never a fresh workspace. If the tool is unavailable
or fails, explain that and follow the documented fallback. Saved-output-only requests
reuse their verified report without new CRM calls.

## Confirmed-profile workflow

When Python is available, retain the complete live responses and run the packaged
report engine before presenting totals, counts, ranking or outputs. Do not calculate
a substitute brief by hand or ask permission to finish required local capture and
computation. Never trim custom-field metadata from a response labelled complete.
A selected pipeline/owner must be reflected in the saved profile and supported
read selectors; inspect the actual connector schema rather than guessing its filters.
Use the computed coverage result, not a claim inferred from the final page alone.


For new shared setup or version-2 settings, read `references/workflow-guide.md` and use
`workflow.py` with the explicit period/revenue scope. This replaces legacy setup/fetch/report
steps for that run; currency, probability, target and read-only contracts remain. Daily
priority views never silently narrow revenue. Quick leaves optional rules/adjustments off.
Valid context resolves identity and timezone. Incomplete core/closed-period reads block
authoritative totals. Legacy version-1 settings retain the sections below until reviewed
migration. Native Docs and schedules require actual host tools and verification.

For a requested forecast or output with missing setup, use `workflow.py setup-check`
before starting it. Explain only the required missing choices, offer Complete setup
(first, the default) or Skip for now, then ask one question at a time. Reuse accepted details; never
save new defaults, scope, targets or adjustments without explicit confirmation.

Before a version-two report-only or delivery-only reply, use `workflow.py reply` and `check-reply` from the
workflow guide. Finish file checks and any evidence capture, then run
`check-reply --emit-markdown` as the last report-building Bash call. Send the text between its marker lines as the
entire final message, with no introduction, separator or extra file summary.
Evidence capture is a verification step: complete it before that final emission.
Do not add a receipt such as “Evidence captured” or “Sending the checked reply”. When the user asks something the brief does not answer, write `answer.json` and pass `--answer answer.json` to both `reply` and `check-reply` (format in the workflow guide); never move the brief into a file to make room for an explanation.
Brief mode retains the computed report; delivery mode
allows a short receipt. Use `schedule-plan` to build a complete proposal before explaining
unsupported scheduling. These local commands perform no CRM or host writes.

The confirmed workflow also preserves the required charts, Sales Coach voice and
interactive choices below. After delivering the checked result, use `workflow.py questions`
and the host's selectable tool for the next unanswered decision. Offer Dashboard / Claude
Doc / Visual artifact / Both / All three / Nothing else where supported. That offer is the
question card from `workflow.py questions`, made right after the checked reply in the same turn;
a sentence after the report such as "Say the word if you want a dashboard" is not the offer. A turn that ends on the checked report with that card not asked is sent back once by the plugin's Stop hook, naming the questions command.
Ask optional risk review and target comparison
separately; never replace them with an “Open decisions” paragraph or show starter
multipliers before a requested review. Honor a target/output answer already supplied.
Question cards are separate from the checked text body. Reuse compatible saved evidence
for local recalculation; explicit fresh and scheduled reads bypass the cache.

When Visual artifact is selected, read `references/visual-artifact-guide.md`.
Build a tailored view from the same saved report; keep the existing dashboard route.

For setup discovery, read `references/account-context-guide.md` and prefer the host’s
account-context tool when exposed. Save it in a separate setup run; reuse confirmed settings.

Before recording MCP responses, read `references/response-capture.md`. Keep original
response files beside the run immediately, including when publishing later fails.
Never reconstruct raw rows from a summary or refetch solely to save a response.

## Critical

- Run the bundled scripts, never read them: each call is one plain `python3 <skill folder>/scripts/<script>.py …` command, nothing before `python3` and nothing after the arguments: no `cd`, `ls`, `&&`, `;`, `|`, `$(…)`, redirects, heredocs or `echo`. Some clients refuse anything else with a message that sounds like shell access is off for the whole session ("denied … don't ask mode"). It is per command: rerun the same script call in the plain form. A refusal never means the forecast may be computed by hand; say which command was refused and retry it plainly. Only a command refused in the plain form starts "No script available".
- Read `references/company-url-guide.md` for the shared company URL setup. Before a new interactive report, ask for a missing company URL after scope/owner confirmation, with Skip for now. Reuse valid saved `output.domain` from any of the three skills without asking again; preserve it on settings updates. Scheduled runs and pending write confirmations never ask this question.
- For an unanswered interactive choice, read `references/question-guide.md` and use the current host's selectable question tool when exposed (`AskUserQuestion` or its documented equivalent), with a short numbered chat fallback. Respect typed answers; skipped or automatic answers are not consent. Do not ask again for a choice already given.

- Read `references/coach-voice.md` for Sales Coach tone around the calculated figures. Identify the supported gap or concentration and one assumption worth validating; keep the leadership-ready section neutral. Do not convert a coaching observation into a forecast adjustment or a CRM write.

- Never writes to Pipedrive.
- Never invents targets, probabilities, exchange rates, or a manager commitment.
- Never silently sums currencies.
- Refer to owners, pipelines, stages, filters and deals by name and verified link, including when explaining missing evidence or a needed re-read. Never print internal IDs (`owner_id`, pipeline or filter numbers, `Deal #N`); use the names in the compute result's `scope_label` or account context.
- Do not use this skill for daily prioritisation: use `pipeline-brief`. Do not use it to create or propose CRM follow-up actions: use `deal-triage` after an explicit request.
- Before any dashboard renderer or publication, run `scripts/artifact_delivery.py profile` with what this session shows (`--artifact-tool`, `--local-folder`; `references/presentation-guide.md`). A `.pipedrive-sales/client-capabilities.json` file or the user's description of it is not a validated profile and cannot authorize delivery. If the profile returns no delivery option, keep the saved text forecast; do not render.
- Never assume a setup answer, whether scope, owner, sales motion, timezone, filter, period, trials-or-demos choice or the confirmation to save. When saved settings are incomplete or an answer is missing, ask it with the selectable question tool (`references/question-guide.md`), one question at a time, and save only answers the user gave and confirmed. With complete saved settings go straight to the report.

## No script available

When the turn's tool list has no Bash (or no `python3`), or the first script command is still refused in the plain form (a refusal is per command: the first Critical rule), compute the forecast in-model from the rules in this file and `references/`. Open the reply with this line, exactly:

Not code-checked: this client could not run the skill's scripts, so the figures are the model's own reading of the rules.

Say why in one sentence (no Bash tool, no `python3`, or the refused command). Every output carries that label. Apply the calculation rules in-model (probability precedence, one section per currency, targets from the saved settings, the stage-order notices, as "Fetch and calculate" in `references/legacy-procedure.md` states them) and keep the section 3 shape. Print the line that names the open deals whose close date is outside the period or missing, with their count and value. Print the four figures per currency under the labels `scripts/compute.py --markdown` prints: "Won to date", "Likely revenue this period" (the adjusted forecast including won revenue), "Weighted forecast" and "If every included open deal closes" (the best case); with no usable probabilities, print "Value including won revenue" and "Open deal value" in place of the second and third. Offer no dashboard, Claude Doc or visual artifact. Write no checkpoint, no history entry and no saved result, and prepare no triage handoff from it.

## Saved evidence and dates

Save tool responses unchanged with their parameters and observed timestamp; never rebuild
responses from older rows or patch selected fields to imitate a fresh read. Keep setup
calls in a separate discovery run. Cached evidence retains its original timestamp and is
not counted as a new call. Mark its freshness limits explicitly. A date rollover creates
an older snapshot, not a retroactive date error. Preserve completed activity scheduled
dates and marked-done timestamps separately using `scripts/activity_evidence.py`.
Explain which date supplies the recency proxy; neither proves buyer contact. Copy close
dates from computed items exactly. Risk flags suggest review; they do not prove a date
is a placeholder, a deal will slip, or an activity was buyer contact.

When the user asks to "turn on the tester footer", set top-level `"tester_footer": true` in `./.pipedrive-sales/config.json`, keep every other key, read the file back and confirm in one line.

## 1. Start with the user's question

Ask for missing period and scope information before the first result, plus the optional company URL described in `references/company-url-guide.md` (Skip for now keeps the first estimate unblocked). Reuse the
period and whose deals to include when the request or confirmed saved settings already
provide them; do not ask again. Resolve relative periods against today's date and the
saved fiscal calendar. If scope is missing, ask one plain question: “Your deals, the
whole team, or a particular pipeline?” Do not silently widen a personal request to the
team or include an excluded pipeline. If “my deals” cannot be matched to a confirmed
owner, ask for the owner rather than guessing.

A missing target, manager commitment, risk adjustment or probability ladder does not
block the first estimate. Do not turn these optional settings into a setup checklist.
Use “whose deals” in conversation instead of “lens”, and “risk adjustment” instead of
“haircut”. Read `references/setup.md` for the short first-run flow and later settings.

Before fetching CRM data, read `./.pipedrive-sales/config.json` from the current workspace when it exists. Pass that exact path to `scripts/compute.py --config`; do not replace a readable configured target or haircut with defaults. Forecast owns only `forecast` settings and preserves every shared or unknown key. Fiscal year starts in January unless confirmed local settings say otherwise; disclose it in the footer.

If no config exists, use an empty JSON object saved alongside this run's evidence as
the compute config, with the explicitly confirmed scope passed through `--lens`.
Do not install starter adjustments or save optional settings merely to run a forecast.

Targets and Manager commits are entered locally by period, currency, and lens. Never treat a missing target as zero. A team run may show team headline plus owner and pipeline breakdowns, but never adds overlapping targets.

## 2. Fetch and calculate

On the confirmed-profile workflow (section 1, `workflow.py`) every read comes from `workflow.py plan`, including the completed-activity evidence (a per-deal read by activity ids): never make a scope-wide `getActivities` read, never page a read by a time window of your own, and run `plan` again after every batch until it returns no reads; `report` refuses while a planned read is still uncovered, and after its page cap the script itself reports the unread cursor as partial activity coverage. When the user states an as-of date, pass it as `--today <YYYY-MM-DD>` to every script and use it for every "days since" and overdue statement.

For version-1 settings only (saved before version 2, never a fresh workspace), read "Fetch and calculate" in `references/legacy-procedure.md` before the first CRM read: the stage and as-of inputs, the paged activity read, the run record, the compute command, the evidence stops, the probability precedence and the stage-ladder offer the script prints, the stage-order rule and the risk-adjustment lines are all there, unchanged.

## 3. Output and scheduled mode

Run `scripts/compute.py --markdown` and send the text between its marker lines unchanged, including the one line "<N> open deals (<value>) have close dates outside <period> or none, so they are not in this forecast." that names the open deals the period leaves out; never say the forecast is built from all fetched open deals. If the script exits with an error, it printed no forecast: tell the user the forecast was withheld because its reply check failed, name each failed check from stderr in plain words, and never write the figures yourself. Each currency starts with won revenue, likely revenue, the matching target, and the projected shortfall or surplus; it also states the amount still to win after won revenue. Best case means every included open deal closes. Label Manager commit as a separate entered judgment, and say when in-scope deals cannot support it. Show weighted forecast alongside the main numbers. Keep assumptions and evidence limits concise, with at most five largest named adjustments plus the remaining count; retain the full per-deal audit in JSON. Keep calculation evidence inside needs-attention cards, and omit the global calculation section from chat, dashboards and Claude Docs. If every included deal receives the inactivity adjustment, explain the saved threshold and offer a review; never silently change it. Never call the calculated number “commit”. Use computed per-deal haircut impacts for any numeric explanation; do not estimate or recalculate them in prose. A scheduled next activity does not clear a silence adjustment, which uses completed activity history. Keep deal-level follow-up recommendations for an explicit Deal Triage run.

Lead with the computed amount and its main limitation before offering optional settings.
Explain a weighted estimate as “Based on the chances recorded in Pipedrive”, and retain
the named local assumptions when an accepted ladder is used. An unweighted result is
open deal value, not a likelihood estimate; keep that distinction in text and dashboards.
After a result with no target, offer “Compare this with a sales target?” Do not ask for
a manager commitment, custom probabilities, retention or fiscal settings unless needed
for the user's request. Optional questions must not interrupt delivery of the result.

Scheduled mode uses saved settings only. It is text-only: never ask questions, offer a ladder, edit a setting, or create history from pasted data. Read config, fetch the required CRM evidence, persist the exact run record, invoke the compute command above with `--scheduled` (it prints no ladder offer), and return the result without exploratory source reads or extra setup. A missing optional target stays “not configured”; if a required setting is absent, return one clear configuration message and stop. Scheduled mode must never offer a dashboard.

## 4. Optional dashboard and triage handoff

After every completed interactive forecast, offer **Dashboard / Claude Doc / Visual artifact / Both / All three**.
Read `references/presentation-guide.md` for native document creation, design choices,
branding, tested chart composition and delivery verification. Combined is the default;
explain its tradeoffs and the optional Claude-designed mode. Use the same completed saved
Forecast JSON for all outputs: `./.pipedrive-sales/results/forecast-full.json`, which the compute command's `--output` wrote (`workflow.py report`, or on version-1 settings the command in `references/legacy-procedure.md`) (no second compute run, no CRM calls); scheduled runs remain text-only. `Prepare triage` selection and copy controls make zero additional Pipedrive calls and only prepare a chat handoff; only an explicit request starts Deal Triage.

An explicit dashboard request uses Combined without another design-selection question.
After an output-offer selection, use `references/question-guide.md` for the optional style choice. Honor an explicit surface
or design choice already given. Keep advanced options available without requiring the
user to configure them before seeing a useful report.

When publishing an older saved result, call it a dated snapshot and do not claim the deals
are still open without a fresh read. Do not promise that a future recompute will reuse
the same Artifact link unless the host confirms an update to that exact Artifact.
