# Setup — pipeline-brief

Read this file only when a required config key is missing. Ask in plain language, one
question at a time, with one sentence of explanation. Every question has "skip for now".
Keep answers in this conversation. Do not write `.pipedrive-sales/config.json` directly:
after all required answers are confirmed, use `save_verified` once for the complete config
and read back that exact path before saying it is saved. Use `update_verified` for
`last_run` and every later confirmed change. Use `./.pipedrive-sales/config.json` when
available; use `~/.pipedrive-sales/config.json` only when the project location cannot be
used. The home path is not a backup. If neither path can be verified, say the choices are
session-only and do not claim a saved `last_run`.

The required first-run sequence is discovery, pipeline confirmation, personal-owner
confirmation when applicable, priority style, priority fields (skippable), silence threshold, then no-next-activity
preference, then the company URL only if neither saved settings nor authenticated
account context supplies it (with Skip for now). Read `references/company-url-guide.md`
and `references/account-context-guide.md`. Pipeline and owner names discovered from
account context may be saved as metadata before selected scope is confirmed; this does
not complete setup. Everything else stays off (null) until set — a null flag threshold disables
that rule; presentation defaults are allowed and are printed in every footer.

| # | Question | Config key |
|---|---|---|
| 0 | **Scope.** After account discovery, if any pipeline is suspected non-sales, use its verified name: `<pipeline name> (<first stage> → <last stage>) looks like a non-sales pipeline. Include all pipelines, or sales pipelines only?` Keep IDs internal. Disambiguate duplicate names with verified stage ranges; if still ambiguous, ask for a selected-view URL. Only after unavailable/failed context discovery may a stage range stand in for an unavailable name, with that limitation stated. Present selectable choices when supported. Until the user answers, every discovered pipeline remains in scope. Never state a planned exclusion as a default. A demo title or zero value is never a reason to exclude it. | `scope.pipeline_ids`, `scope.exclude_pipeline_ids`, `pipelines` (internal id → verified pipeline name) |
| 1 | **Whose deals (every new scope).** After the pipelines answer, ask “Whose deals should the brief cover: yours, the whole team's, or a named teammate's?” with options Me, Team and the named teammates, unless the scope already names `owner`. When the request itself says "my deals", "only my deals" or "mine", the answer is Me and the answers file records `owner_source: "request"` (see workflow-guide: nothing else is inferred from the request). A saved version-2 profile with no owner is asked only this question, and the answer is saved into that profile. Version-1 settings (no `schema_version`) are not asked: there `scope.owner_ids: []` means the whole team. Use authenticated `current_user.id` when account context supplies it; no note identity probe is needed. Only after unavailable/failed context discovery, read at most one recent note. Use its `user_id` only when joined to the nested user identity, then ask: “I found **[name]** — is that you?” Keep the identifier internal. Do not make an owner-scoped ordinary deal fetch until fallback identity is confirmed. If no joined identity exists, ask for a saved “my deals” filter or an explicitly labelled team view. Never match records by position. | `scope.owner_ids`, `owners` (id → name) |
| 2 | How does your team decide what to work on first? (a) warmest and biggest deals first — most engagement, highest value, latest stage; (b) newest first — brand-new leads or signups with no contact yet get called before anything else; (c) both — newest first for new leads, warmest first for deals already in progress. *Explain: teams differ; a subscription seller calls new signups first, a project seller works the hottest deals first. The skill orders everything by this answer.* | `sales_motion`: `engagement_led` / `speed_to_lead` / `hybrid` |
| Fields | **Priority fields.** “Which Pipedrive fields do you look at to decide what to work on?” Run `python3 <skill folder>/scripts/account_context.py --input <saved context response> --deal-fields` and offer its `options`: the account's own deal field names, at most 15, never invented. Accept up to 15. A typed name resolves only against the `fields` list in that output; a name that is not listed, or that two fields share, is asked again. Skip for now leaves the deal lines unchanged. The chosen fields appear on each deal line; they do not change the ranking or the filtering. | `priority_fields` (list of `{key, label}`) |
| 3 | Silence threshold in days. *Explain: "no reply in N days" is the most common early warning; pick what your sales cycle treats as too quiet.* | `silence_days` |
| 4 | Flag deals with no scheduled next activity? *Explain: Pipedrive's own discipline is that every deal has a next Activity.* | `flag_no_next_activity` |
| URL | What is your company Pipedrive URL? Accept a URL or subdomain; reuse a valid saved value without asking. Skip for now keeps plain deal labels. | `output.domain` |
| 5 | Days in a stage before it counts as stale. If Pipedrive rotting is on for a stage, propose its days-to-rotten ("Your stages rot after 15 days — use that?") and ask to confirm; otherwise ask for one default, per-stage optional. | `stage_stale_days.default`, `stage_stale_days.by_stage_id`, `stage_stale_days.use_pipedrive_rotting` |
| 6 | Days of slack after expected close before flagging a slipped close. | `close_slip_slack_days` |
| 7 | Minimum deal value to appear in the ranked list. Should the ranking favour deal size, closeness to closing, or how long a deal has been quiet? (Default: balanced — the weights are shown in every brief and can be changed any time in plain words.) | `value_floor`, `ranking.{value,probability,depth,neglect}` |
| 8 | Flag deals whose notes say won/lost/signed but the stage hasn't moved? (yes/no) | `flag_notes_stage_mismatch` |
| 9 | Include the Leads inbox? Then, separately if wanted, set output style, language, currency, and the advanced Pipedrive response size cap. Pipedrive response size means bytes in one MCP response; AI context reserve is model capacity kept for validation, evidence, and rendering; Pipedrive account allowance is a server-provided account limit, shown only when the MCP exposes it. A saved Pipedrive filter is the effective scope; do not say a separate pipeline exclusion narrows it. | `scope.include_leads`, `scope.filter_id`, `output.style` (`auto`/`short`/`full`), `output.language`, `output.currency`, `fetch.max_response_bytes` |

After each successful run, use `update_verified` to write `last_run`; report it as saved
only after read-back from the same verified path.

**Setup review.** When the workflow returns a `confirm_setup` question, show its `review` text exactly as printed, stage-order lines included ("Stage order not available for …"). Do not reword it, reorder it or summarise it in your own words. Its `review_sha256` is for the confirmation only; never show it.

## Plain-language tuning (any later run)

"Value matters more than staleness", "ignore deal value, we sell flat-rate", "cap deals
under £1,000", "staleness matters more": map to a change in `ranking.*` or `value_floor`,
restate what changed and the new weights, then use `update_verified` on confirmation.

## First-run discovery (before Q1)

1. Use account-context discovery in `account-context-guide.md`: list real pipeline
   names and nested stage ranges (in Pipedrive's `order_nr` order when it is supplied,
   otherwise as returned), without deal examples or a notes identity probe. No stage tool
   exists; when the context is unavailable, say stage data is unavailable and stop
   (`troubleshooting.md`). Quote each pipeline's range from the script, never from the raw context: run
   `python3 <skill folder>/scripts/account_context.py --input <saved context response> --pipeline-ranges` and use its `label` values
   exactly (the raw names carry stray spaces and full stops, for example "Handover."). Stage names can suggest non-sales scope; deal title and value
   cannot: a demo title or zero value must stay visible and must not justify exclusion.
2. If any pipeline is suspected non-sales, ask the matching named or unnamed Q0 scope question and wait. Do
   not ask about motion, owner, Leads, silence, or no-next-activity first. Until answered,
   all discovered pipelines remain in scope and no scope or `last_run` value is persisted.
3. After scope confirmation, for “my deals” with no saved owner ID, use authenticated
   `current_user.id` from account context. Only when it is unavailable, read one recent note.
   Its `user_id` is usable only with a nested `user` identity in that same note; ask for
   confirmation before using it as `owner_id`. Do not use list position. For team setup,
   build `owner_id → {name, email}` from later notes; owners who never wrote a note stay
   as IDs until named here.
4. Preserve verified pipeline names saved by account discovery. Never replace a pipeline
   name with its first stage; two pipelines may share the same stages. Where names are
   unavailable after failed/unsupported discovery, use a verified stage range with the name limitation stated. Keep IDs internal. Add confirmed `pipelines`, `scope`, `owners`, and `scope.owner_ids` to the complete
   in-conversation config. Persist only after Q2–Q4 are also confirmed, then show both
   `Settings saved:` and `Verified settings:` from the same read-back.

## Tester footer

Testers can set `"tester_footer": true` in `.pipedrive-sales/config.json` (off by default, version-1 and version-2 settings). Each result then ends with one line, `Tester: <skill> <version> · <N> calls · ~<T> tokens of Pipedrive data read by the scripts (estimated)`. Every saved result carries `skill`, `version`, `calls` and `estimated_tokens` whether or not the footer is on.
