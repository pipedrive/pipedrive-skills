# Legacy procedure details (version-1 settings)

These rules sat in SKILL.md until release 0.7.0 moved them here to keep the body under 300 lines; the wording is unchanged. Read the section you need: "Saving settings" before the first settings write (section 0), "Pipelines, personal scope and owner names" before the first scope question (section 1), "Overdue work" for an explicit overdue request, and "Computation definitions", "Aggregates" and "Output shape by record count" when computing or writing a brief without `compute.py` (section 3 to section 5). Open-deal paging, the response-size policy and 429 recovery moved to `references/fetch-contract.md`, which Deal Triage shares.

## Saving settings (section 0)

After first-run setup or an approved change, pass the complete config through
`scripts/settings.py` / `save_verified`; use `run_state.py` for skill checkpoints and `update_verified` for every
later confirmed setting change. Read back that exact path before saying it was saved.
State the result as `Settings saved: <path>` and `Verified settings: <complete key values>`
only when it returns `persisted: true`. If neither location can be verified, say the
choices are session-only, do not claim settings or `last_run` were saved, and keep them
only for the current conversation.
For a settings-only request, put those two labelled lines in the final answer and name
each confirmed choice in the verified line; do not replace them with a generic success
message.
Never silently switch locations between sessions. Read the verified project path when it
exists; otherwise read the verified home path.

## Pipelines, personal scope and owner names (section 1)

- **Pipelines first:** group the context's stages by `pipeline_id` and pass the saved context
response to compute with `--stages <response file>`. If config lists a confirmed pipeline but
the context has no stages for it, return a stage-data warning before any deal reads; never treat
missing stage data as an empty or healthy pipeline. If the context is unavailable, say stage
data is unavailable and stop (`references/troubleshooting.md`). Show each pipeline's name and
stage range. Stage names may make a pipeline *suspected* non-sales
(for example, "Case opened → Resolved"), but a demo-titled or zero-value deal never
does. Until a user answer, state that every discovered pipeline remains in scope; do
not save `scope.pipeline_ids`, `scope.exclude_pipeline_ids`, or `last_run`. If any
pipeline is suspected non-sales, choose the Q0 wording by available metadata:
with a verified name, ask `<pipeline name> (<first stage> → <last stage>) looks like a non-sales pipeline. Include all pipelines, or sales pipelines only?`;
without a verified name, ask `Pipeline <id> is <first stage> → <last stage>. Include all pipelines, or sales pipelines only?`.
Use selectable choices through `references/question-guide.md` when available. Add the ID
when names collide; stage ranges support the name, never replace it. Wait for the answer before
asking about motion, owner, leads, silence, or no-next-activity. Never present a
planned exclusion as a default. After confirmation, add the confirmed `scope` and
`pipelines` (labelled by verified pipeline name, or by stage range with ID when a name
is unavailable) to the in-conversation complete config. Preserve names already saved
by account discovery; never replace them with the first stage name. Do
not persist it yet. With `scope.filter_id`, report the saved filter as the effective
scope and do not claim a separate pipeline exclusion narrowed it. Always name confirmed
excluded pipelines in the header. In the setup reply, explicitly say that all discovered
pipelines remain in scope until confirmation, and that a demo title or zero value is not
an exclusion reason.
- **Personal scope before ordinary owner-scoped reads:** after pipeline confirmation, when
the user asks for “my deals” and `scope.owner_ids` is empty, resolve identity before an
ordinary owner-scoped `getDeals` call. A saved, verified owner identity wins. When authenticated account context is unavailable, make at most one small recent-notes read:
`getNotes(limit=1, sort="add_time DESC")`. A note may identify an owner only when its
`user_id` is joined to the nested `user` object in that same note (`user.id` matches, or
`user.is_you` is true). Ask the user to confirm the resulting name and owner ID before
saving it or applying an `owner_id` filter. When the user confirms that discovered
identity in a later turn, continue the personal brief with that owner filter before
the first ordinary open-deal read; do not repeat the identity question. If the note
lacks a usable joined identity,
ask for their Pipedrive owner ID, a saved “my deals” filter, or whether they want an
explicitly labelled team view. Never match records by position or fetch a full team page
under a “my deals” label while identity is unresolved.
- **Owner:** if `scope.owner_ids` is empty, use the owner pinned in a scheduled-task
prompt. For a personal request, follow the early identity step above. For a team request,
after the notes fetch (section 2 step 7), build `owner_id → name` from the notes' `user.name` /
`user.email` (match only within that note; `is_you` is a hint) and ask once "which of
these is you, and what should I call the others?" Write to `owners` only after the user
confirms. Never print a bare ID where a name is known. Unresolved in scheduled mode →
run team-wide and say so.

## Overdue work (section 2, step 4)

4. **Overdue work:** do not fetch account-wide undone activities in a normal brief. Only when the user explicitly asks for overdue work, use a bounded `getActivities(done=false, sort_by="due_date", sort_direction="asc", limit=<cap>)` scan, discard unlinked rows,
and label its count partial unless every relevant page was read. Never use an undeclared
due-date filter. An ambiguous rep request means **self** (the accepted account-context user,
or confirmed local identity), never team-wide. An explicitly named owner means
**named-owner** scope; a manager's team request means **team** scope. State that exact scope
in the overdue output before the activity list. After this scan, run
`python3 <skill folder>/scripts/compute.py --mode overdue --config <config path> --input <run file>`
and paste its output; it filters to linked activities on fetched open deals.

## Computation definitions (section 3)

- days_silent = today − max(due_date of the most recent done activity,
last_incoming_mail_time); null if none. Preserve marked_as_done_time separately as the recorded completion timestamp; use it only as a labelled fallback when a completed activity has no scheduled date. update_time is a
fallback only and is said to be one.
- days_in_stage = today − stage_change_time (fallback add_time; "never moved" is itself
a finding).
- has_next = next_activity_id != null.
- close_slipped = expected_close_date < today − close_slip_slack_days.
- stage_stale threshold = days_to_rotten when the stage has rotting enabled and
`use_pipedrive_rotting` is true; else config.
- priority (engagement_led) = w_v·sqrt(value/max_value) + w_p·p + w_d·stage_depth
  - w_n·min(days_silent/90, 1), weights from config.ranking; stage_depth =
  order_nr/max order_nr in the deal's own pipeline (no `order_nr` from Pipedrive: skip depth,
  R5 and the first-stage rules for that pipeline and print one line per pipeline, "Stage order not
  available for <pipeline name>; stage-order rules skipped."); p = deal probability, else stage
  probability if stage probabilities vary, else omitted and stated. Skip deals below
  value_floor. speed_to_lead: untouched leads and first-stage deals with no activity
  rank above everything, newest first; the rest as engagement_led. hybrid: speed_to_lead
  for leads and the first stage; engagement_led from the second stage on.
- at_risk = SILENT or NO_NEXT, whatever the priority.
- buying_signal = a kept note contains buyer-side commercial language in the maintained
language pack in references/rules.md. Evidence only; never changes the score. A language
without a maintained pack is **unavailable evidence**, not a negative conclusion.
- Flags: R1 SILENT, R2 NO_NEXT (only if flag_no_next_activity), R3 STAGE_STALE,
R4 CLOSE_SLIPPED, R5 SINGLE_THREAD (participants_count ≤ 1 and stage ≥ configured),
R7 NOTES_MISMATCH (only if checked). Snoozed deals (config.snoozed) are excluded until
their date.
- Clusters: same add_time to the minute + same stage + zero activities → one
import-cluster row; integration shells (origin/channel_id from an app AND no contact
AND no notes AND no activity) → one row. Never rank cluster members individually.

## Aggregates (section 4)

Open count and value by stage; movement in window (created, entered stage, won, lost);
closing this week = open deals with expected_close_date from today to Sunday of the
calendar week that contains today (Monday to Sunday, in the rep's timezone; the window and
dates are in `closing_this_week.week`, printed as "Closing this week (12–18 Oct)"). Count,
value, which are also at risk. Open deals whose close date has passed are not counted here;
they carry the "close date passed" flag; overdue activities by owner (manager scope only); value
concentration (if one or two records hold most of the open value, say so before ranking;
if the top records are all 0, say the value field is unfilled and cannot drive the
ranking). Sum per currency; convert only with rates in config.

## Output shape by record count (section 5)

- **Under ~30 in-scope records — recall mode.** Lead with the checked count, then use
prose without buckets: the one or two records that matter now and what to do; anything
forgotten (no next step, lapsed
next step, never opened); anything the CRM has wrong. If it comes out as a cleanup
list, say so — do not manufacture a ranking.
- **~30 to the budget — triage mode.** Lead with the checked count, scope, and any
partial-read warning. Show open value, useful movement or close dates, and up to five
named actions with a plain-language reason and next step. Keep per-deal calculation
details in Needs Attention cards. Store rule evidence, call counts, and defaults in
the saved result rather than the visible brief.
- **Above the budget — narrow or continue.** Name what was checked and what was not
fetched. Offer to continue toward a full scan in bounded batches using the saved cursor,
or narrow the next brief to a pipeline or saved filter. Give an exact remaining count
only if the response supplied one. Offer the scheduled digest when useful.
- **Never empty.** Walk the ladder in references/output-format.md until there is
something true and useful; with no open records, report what closed most recently and
which dormant records to reopen, by name.

## Evidence notes

Activity evidence: the bounded top-30 activity lookup does not prove that other deals
have no completed activities. Carry the computed unresolved-date count into chat,
dashboards and Claude Docs. Label deal-update fallbacks clearly and recommend checking
activity history before treating an update as buyer silence.

For a legacy settings run, retain the current account-context response in its separate setup ledger and refresh names in the run-only settings copy with `account_context.py` before computation. Preserve accepted scope and checkpoints. If context is included in the run metadata, the engine refreshes display names without writing settings. Do not discard discovery just because field metadata is large. A bounded top-30 activity lookup is not full activity coverage: retain the unresolved count. Offer additional batched activity reads from the already saved deal IDs when the remaining budget permits and the user wants fuller evidence; do not fetch all deals again.
