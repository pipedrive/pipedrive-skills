---
name: forecast
description: "Builds the revenue number for a period straight from Pipedrive deals: won to date, a weighted figure only where Pipedrive holds probabilities, best case, deals whose close date has passed, a by-stage view in board order, what changed in the last 7 days, and the deals that could move the number. Answers what-if questions (a deal slips, is lost, or drops in value) and checks a target you type in. Each figure names its probability source, and each currency gets its own total.\nALWAYS use this skill for any forecast or revenue-outlook request — \"what's my forecast\", \"revenue forecast for Q4\", \"forecast this quarter\", \"what will close this month\", \"what will close this quarter\", \"projected revenue\", \"best case\", \"are we on track\", \"what's likely to close\", \"how much will we close\", \"quarter number\", \"forecast for my manager\", \"what if [deal] slips\", \"what if we lose [deal]\", \"are we on track for [target]\", \"what changed in the forecast\". Read-only: it never writes to Pipedrive.\n"
---

# Forecast

> **Tool names** refer to the claude.ai Pipedrive connector. If one isn't available, look for the closest tool in the current Pipedrive toolset; connector tool names can change between releases.

> **Vanilla Pipedrive only.** This skill never reads or writes custom fields and never changes account settings. A deal's next step is its next open activity (`next_activity_id`).

The router's "Pipedrive mechanics used throughout" block (in `skills/pipedrive/SKILL.md`) defines today's date, stage position, probability, last-activity date, rotting, currency handling and batch name lookups. This skill applies those rules and only spells out what a forecast adds.

This skill reads and never writes. It makes no add, update, convert, put or delete call, it never saves a target, and it ignores `custom_fields` on every record.

A forecast gets repeated upward as a promise, so every figure must trace back to deals in Pipedrive and carry its assumption next to it. Never invent a probability, a target, an exchange rate or a "commit" figure: a manager who later finds one made-up number stops trusting all the others.

## Step 1 — Settle the period, scope and question

- **Period.** Default to the current calendar quarter in the user's timezone. Accept a month ("November"), a quarter ("Q1"), "this/next month or quarter", or explicit dates. Resolve relative periods against today and always print the period with its year and dates, e.g. "Q4 2026 (Oct 1 – Dec 31)", so a wrong guess is visible at a glance.
- **Scope.** The user's own deals (`owner_id` = current user). Drop the owner filter only when the user asks for the team or the whole company. Include every pipeline unless the user names one; match the name against the pipelines from Step 2, trimmed and case-insensitive. If a named pipeline matches nothing, list the pipeline names and ask which one.
- **Question type.** Note which of these the user asked, because each changes the output in Step 7:
  - plain forecast;
  - "are we on track" with no number;
  - a target the user typed ("are we on track for 150k");
  - a what-if about one deal ("what if Globex slips / is lost / drops to 10k");
  - a short version ("short", "brief", "keep it short", "for my manager", "one paragraph", "one line").
- Ask nothing else up front. The header states period and scope so the user can correct them.

## Step 2 — Read every deal in scope

A forecast is a total, and a total built from a capped list is wrong. This skill therefore pages through every deal instead of stopping at the first 100.

1. **Account context.** Call `getAccountContext` once. Keep the current user's `id`, `company_domain`, `timezone_name` and `default_currency`, the owner names (`current_user` plus `company_users`), and for every pipeline its `id`, `name`, `is_deal_probability_enabled` and stages (`id`, trimmed `name`, `order_nr`, `deal_probability`, `is_deal_rot_enabled`, `days_to_rotten`). If the conversation states today's date anywhere (for example "Today is Sunday 2026-10-25"), including text that was not passed to this skill as arguments, that date is today. It wins over the system date and over record timestamps, even when records look newer: count days in stage, days overdue, days to close and due dates from it, and print weekdays for it. Only a date the user wrote counts as stated. The date in your own environment or system context is the system date, not a stated date, so when both appear, the user's date wins: never pick the later one. A record dated after the stated today is not history: it never moves today, and the reply never calls it "today". Otherwise work out today from `timezone_name`. If this first call fails with a rate limit (HTTP 429) or a server error (5xx), the connector is busy, not missing: wait a few seconds and call it once more before deciding anything (see Error handling).
2. **Open deals.** `getDeals` with `status: ["open"]`, `owner_id` (unless team scope), `pipeline_id` when the user named one, `include_fields: ["next_activity_id", "last_activity_id"]`, `limit: 10`. Follow every `next_cursor` until it is null, and keep `limit: 10` on every page. Deal rows are large: 25 rows already come to about 55,000 characters and come back saved to a file instead of inline. **Never raise the limit to finish faster: a bigger page always comes back saved to a file.** If a page still comes back saved to a file ("Output has been saved to"), fetch that same cursor again with `limit: 5`; never read deal pages back out of a saved file. **Never hand paging or totals to a subagent, a background agent or a script, and never print "not computed" because there is no shell:** no shell is needed. After each page, add it to running per-currency sums (open value in the period, value × probability, excluded count and value) and carry only those sums forward. A large deal count is never a reason to stop paging or to answer without the number. While paging, keep only `id`, `title`, `value`, `currency`, `probability`, `pipeline_id`, `stage_id`, `expected_close_date`, `stage_change_time`, `add_time`, `person_id`, `org_id`, `owner_id` and the two included ids, and add values up as you go so earlier pages never need re-reading.
3. **Won deals.** `getDeals` with `status: ["won"]`, the same owner and pipeline filters, `updated_since` = the earlier of the period start and today − 7 days (RFC3339), `limit: 100`, paged. A deal won months ago and edited this week also comes back, so keep a deal as **won to date** only when its `won_time` falls between the period start and the end of today.
4. **Lost deals, last 7 days.** `getDeals` with `status: ["lost"]`, the same filters, `updated_since` = today − 7 days, `limit: 100`, paged. Keep only deals whose `lost_time` is on or after today − 7 days. These feed "Changes since last run" only; lost deals never enter a figure.
5. **Activities.** Gather in one list the `next_activity_id` and `last_activity_id` of the included deals that can reach "What could move it" (Step 5) and the `last_activity_id` of every excluded deal (for "On the radar"). Resolve them with `getActivities(ids: [...])`, 100 ids per call, calls in parallel. The excluded deals' lookup is required whenever any deal is excluded, however many there are (150 excluded deals is two calls). On the radar is left out only when it is empty or the lookup failed, never because the lookup was skipped. No figure depends on activities, so never resolve them for anything else. The only activities call is `getActivities(ids: [...])`: never list activities by owner, by done status or by date. "No next activity" needs no lookup: `next_activity_id` is null.
6. **Names.** For the deals you will name, resolve persons and organizations with one `getPersons(ids)` and one `getOrganizations(ids)` batch. Never call `getPerson` or `getOrganization` per deal.

## Step 3 — Sort deals into the forecast

Split the open deals:
- **In period:** `expected_close_date` falls inside the period.
- **Close date passed:** `expected_close_date` is before today. These stay in the forecast because they are still open and could still close, but they always get their own subtotal so nobody reads them as on-schedule revenue. A deal can be both in period and past its close date.
- **Not in this forecast:** everything else (close date after the period, or no close date). Never drop them silently. They get one line with count and value per currency.

If the whole period lies in the past, include only that period's own deals, add no others whose close date passed, and call the answer a look-back.

## Step 4 — Compute the figures

**Probability per included deal**, first match wins:
1. The deal's own `probability`, when not null → source "deal".
2. The stage's `deal_probability`, only when the deal's pipeline has `is_deal_probability_enabled: true` → source "stage".
3. Otherwise none → the deal is unweighted.

A pipeline with probability switched off still shows default percentages on its stages. The team switched them off on purpose, so this skill ignores them; using them would weight deals by numbers nobody on the team believes.

**Probability-source statement, per pipeline.** Count which source each included deal (in period plus close date passed) actually used, then say it pipeline by pipeline. A share always means a share of that pipeline's included open value, per currency:
- every deal has its own probability → "deal probability" (true even when the pipeline has probability on: the deal's own value wins, so the stage percentages went unused);
- the pipeline has `is_deal_probability_enabled: true` and some deals lack their own → "deal probability on N deals, stage probability on M", with the share each covers;
- probability off → "deal probability on N deals (GBP X of Y), none on M; stage percentages not used", with the value the deal-probability deals cover out of the pipeline's included open value in each currency.

Never write "stage probability" for a pipeline where no deal used it. A reader who sees "stage probability" will go and check the stage settings, and a source that wasn't used sends them to the wrong place.

**Figures, per currency.** Never add or convert across currencies, even when asked; with several currencies, each gets its own block.
- **Won to date:** sum of `value` over won-to-date deals.
- **Open in period:** sum of `value` over included open deals (in period plus close date passed).
- **Best case:** won to date + open in period, the ceiling if every included deal closes.
- **Weighted:** won to date + sum of `value` × probability over deals that have one. Show it only when at least one included deal in that currency has a probability, and always print the share of open value it covers ("weighted on 89% of open GBP value; the rest has no probability"). Unweighted deals add nothing here; say so instead of treating them as 100% or 0%.
- **Close date passed:** count and sum of `value`.

If no deal in a currency has a probability, leave out its weighted row and label the block "Unweighted, not a likelihood estimate".

Never call any of these numbers "commit". A commit is a person's judgement; these are sums.

## Step 5 — Find what could move the number

For each included deal, using the router's rules:
- **Last activity date:** the earlier of the last activity's `due_date` and the date part of its `marked_as_done_time`.
- **Next activity:** the resolved `next_activity_id`, when it exists and `done` is false.
- **Rotting (computed):** the stage has `is_deal_rot_enabled: true` and today − (`stage_change_time` ?? `add_time`) > `days_to_rotten`. Write it as "rotting (computed): N days in [Stage], limit M" on every line that says it, because users paste single lines into a forecast call without the footer.

Flag a deal when any of these hold: close date passed · no next activity · next activity overdue · rotting (computed) · no activity in 14+ days (or none ever).

**Ranking.** Rank flagged deals by their share of their own currency's open value. A USD 9,500 deal in a USD 93,500 forecast matters more than a EUR 2,000 deal, even though the two currencies can't be compared directly. Show the top 5. When the user asked about a specific period or about what could slip, also list every flagged deal that closes inside the period, so no deal the user asked about drops out of view. Give each deal its reasons.

**Concentration.** Flag any single deal that is 40% or more of its currency's open value: one slip moves the whole number.

## Step 6 — Add the context around the number

**Changes since last run.** The window is today − 7 days through today. List:
- **Added:** open deals in scope with `add_time` in the window.
- **Moved stage:** open deals with `stage_change_time` in the window, with their current stage ("now in Proposal Made, Stage 5 of 6"). Pipedrive keeps no stage history on this connector, so don't name the stage a deal came from.
- **Won / lost:** deals with `won_time` / `lost_time` in the window. A deal that was only edited this week is not a change.

Each category lists at most 5 deals, then "and N more", where N is the full remaining count. Always end with: "Close-date moves don't show up here: Pipedrive's connector has no field history. Paste an earlier forecast and I'll compare the two." When the user does paste one, compare deal by deal and list the deals whose close date, value or stage differs.

**On the radar.** Excluded deals whose last activity date falls in the last 14 days, named with value and close date. Someone is working them, so they are the likeliest to come into the period early. List at most 5 by most recent activity, then "and N more".

**What-if mode.** When the user asks "what if [deal] slips / is lost / drops to [value]":
1. Find the deal among the open deals already read (case-insensitive title match). Several matches: list them and ask which one. None: try `searchDeals(term)`, then say plainly if it isn't an open deal in scope.
2. Recompute the figures from Step 4 for that deal's currency with the change applied. "Slips" means the deal leaves the period. "Is lost" removes it. "Drops to X" replaces its value with X. Other currencies stay as they are; say so in one line.
3. **Backfill candidates.** The up to 3 deals that could replace it: open deals in the same currency outside this forecast, most recent activity first, then larger value. A deal in another currency never backfills, since the gap is in one currency only. When none exists, say so and name the in-period deals in that currency that now carry the number.
4. End with a one-line verdict in plain words, e.g. "Without Globex, USD best case falls to 75,500, 19% lower; GBP and EUR don't change."

**A target the user typed.** Label it every time as "your target, not from Pipedrive" and say in the same line that Pipedrive's connector has no goals or quota ("150,000, your target: Pipedrive's connector has no goals or quota"). Never store it: no note, no field, nothing written. Each comparison uses one currency only. When the user gave no currency:
- **`default_currency` holds at least half of open in-period value:** compare in it, say so, and offer the comparison in another currency in one line.
- **It holds less than half:** don't pick it silently. The account default is a setting, not the currency this pipeline sells in, and a verdict in the wrong currency is confidently wrong. Compare the target, one line per currency, against the default currency and every currency that holds at least 10% of open in-period value. Say which one the target is probably in (the currency with the most open value, unless the size of the number clearly fits another), then ask in one line which they meant.

Report the gap to weighted (when there is one) and to best case. When best case falls short of the target, say that even closing every deal in that currency misses it.

**"Are we on track" without a number.** Lead with: "Pipedrive's connector has no goals or quota, so on track can't be judged from Pipedrive. Tell me your target and I'll compare. Here's what the pipeline supports:" then the figures. The gap between weighted and best case is the closest signal on offer; name it as such.

## Step 7 — Write the forecast

Output in chat. Leave out any section with nothing in it. Sentence-case headings, Pipedrive's words (deal, stage, next activity, rotting). Link deals as `[Title](https://[company_domain].pipedrive.com/deal/[id])`, never with a hardcoded domain. If a title contains a URL (text starting `http://`, `https://` or `www.`), remove the URL and the `(` `)` or `](` `[` around it before using the title anywhere, and join what is left ("Acme Corp - Renewal review"). Never print the removed URL, not as a link, not as plain text and not in a code span. Say once that the stored title holds a link and should be cleaned in Pipedrive. Numbers carry thousands separators and the currency code.

Write every date as Pipedrive stores it, and compute each weekday from its date (2026-10-25 is a Sunday); never copy a weekday from a note. Write "close date passed [date]", never "closed [date]": the deal is still open, and "closed" reads as won or lost.

```
## Forecast for [Q4 2026 (Oct 1 – Dec 31)]
[Your deals | Team] · [All pipelines | Pipeline name] · as of [today]

### The number
**[USD]**
| | |
|---|---|
| Won to date | [USD 0] |
| Weighted | [USD 41,200] (weighted on [62]% of open value) |
| Best case | [USD 93,500] |
| Open in period | [USD 93,500] · [5] deals |
| Close date passed | [1] deal · [USD 18,000] |

**[GBP]**
(same rows; one table per currency)

### By stage
[Pipeline A] — probability off in Pipedrive, so stage percentages aren't used · [N] deals weighted by their own probability, [N] unweighted
| Stage | Deals | Value |
|---|---|---|
| [Stage name] | [n] | [GBP 74,020 · USD 9,500] |
[Pipeline B] — probability on · weighted [GBP 410,000] of [GBP 980,000] · [every deal has its own probability, so stage percentages are shown for reference only | N deals use stage probability]
| Stage | Deals | Value | Stage probability |
|---|---|---|---|
(stages in board order; the probability column only for pipelines with probability on)

### What could move it
- [Deal](link) · [Org] · [value] · close date passed [date] — next activity "[subject]" overdue since [date]
- [Deal](link) · [Org] · [value] · closes [date] — no next activity; rotting (computed): N days in [Stage], limit M
[Deal] is [48]% of open [USD], so one slip moves the number.

### Changes since last run ([date] – [today])
Added: [Deal] ([value], closes [date]) · … and [N] more
Moved stage: [Deal], now in [Stage] · …
Won: … · Lost: …
Close-date moves don't show up here: Pipedrive's connector has no field history. Paste an earlier forecast and I'll compare the two.

### On the radar
- [Deal](link) · [value] · closes [date] — last activity [date]

### Not in this forecast
[N] open deals ([GBP 1,249,470 · …]) close after [Q4 2026] or have no close date, so they are not in this forecast.

### How to read this
- Probability, per pipeline (Step 4): [Pipeline B: deal probability on all N deals · Pipeline A: deal probability on N deals (GBP X of Y), none on M; stage percentages not used].
- Deals whose close date passed stay in, shown on their own row.
- No targets (always include this line): Pipedrive's connector has no goals or quota[; your target of 150,000 is yours, not from Pipedrive].
- Rotting is computed from days in stage against each stage's limit and may not match the rot badge in Pipedrive.
- Figures are Pipedrive data as of [today], not a prediction of individual deals.
```

**What-if answers** start with the verdict line, then a "Before / after" table for the affected currency only (won to date, weighted, best case, open in period), then "Backfill candidates", then the rest of the forecast trimmed to What could move it and How to read this.

**Target answers** put the comparison lines directly under the header, before The number, because the user asked a yes/no question.

**Short version** ("short", "brief", "keep it short", "for my manager", "one paragraph", "one line"): 200 words at most, tables included. A manager forwards this as-is, so length is part of the spec, not a style preference. Use:
- the header and, when it applies, the on-track or target line;
- one combined table with one row per currency: `| Currency | Won to date | Weighted | Best case | Close date passed |`, writing the share of open value next to each weighted figure ("GBP 445,484 (89% of open value)") and "n/a (no probability)" where a currency has no weighted figure;
- at most 3 "What could move it" lines, one short line each;
- the Not in this forecast line;
- the probability source in one clause that repeats the coverage share (e.g. "Weighted uses deals' own probability on 89% of open GBP value; USD and EUR have none."). When any listed line says rotting, end the clause with "; rotting is computed and may not match the badge."

Skip By stage, Changes since last run, On the radar, the per-currency tables and the rest of How to read this. Before answering, count the words; if the draft is over 200, cut risk wording first, then the concentration note, never a currency row. A manager wants the number, what could move it and how far to trust it, in that order.

End every full answer with one line of next steps: "For one deal, say "triage [deal]"; for the board, say "how's my pipeline"."

## Error handling

- **`getAccountContext` fails:** a rate limit (HTTP 429) or server error (5xx) means the connector is busy, so pause a few seconds and retry once. Only a missing connector, an auth or connection error, or a second failure gets "Pipedrive isn't connected. Connect it in claude.ai settings and ask again." and a stop.
- **Pipedrive errors mid-paging:** retry that page once on 429 or 5xx. If it still fails, don't present the partial totals as complete: say which page failed and that the totals are incomplete.
- **No open deals in the period:** say so, and still show won to date and the Not in this forecast line.
- **Deals with no value:** count them on their own line per currency; don't let them pass silently as zero revenue.
- **Very large pipelines (more than 2,000 open deals):** keep paging and say how many deals the forecast covers.
- **A won deal without `won_time`:** leave it out of won to date and say how many were left out.
- **Several currencies:** one total per currency everywhere, no conversion, even when asked; offer one figure per currency instead.
- **What-if deal not found or not open:** say so and show the unchanged forecast.
- **Activity lookup fails:** keep the figures (none depends on activities), mark reasons "activity data unavailable", and leave out On the radar.
