# Rules — pipeline-brief

Rule ids are shared with `deal-triage` so a user can say "turn off R5" in either skill.
A null threshold in config disables the rule. Every flagged line in the output leads with
the rep-facing risk and next step; its evidence clause cites the rule id and dates that
produced it.

## Flags

| Id | Flag | Fires when | Config | Notes |
|---|---|---|---|---|
| R1 | SILENT | `days_silent ≥ silence_days` with verified contact evidence | `silence_days` | `days_silent` = today − max(due_date of the most recent valid **done** activity, `last_incoming_mail_time`). Never use `marked_as_done_time`; a completed activity due after today is not touch evidence. When at least five past, deal-linked activities are 70% or more incomplete, say “recent contact unverified” instead of `SILENT`. `update_time` is a weak proxy only and must be labelled as one. |
| R2 | NO_NEXT | `next_activity_id` is null on an open deal | `flag_no_next_activity` | Pipedrive's own discipline: every open deal has a next Activity. |
| R3 | STAGE_STALE | `days_in_stage ≥ threshold` | `stage_stale_days.*` | Threshold = the stage's `days_to_rotten` when `is_deal_rot_enabled` and `use_pipedrive_rotting`; else `by_stage_id`, else `default`. `days_in_stage` = today − `stage_change_time`, falling back to `add_time` ("never moved" is itself a finding). |
| R4 | CLOSE_SLIPPED | `expected_close_date < today − close_slip_slack_days` on an open deal | `close_slip_slack_days` | Slipped more than 90 days → also listed under data hygiene. |
| R5 | SINGLE_THREAD | `participants_count ≤ 1` and stage order ≥ configured stage | `single_thread_from_stage_id` | Off in the brief unless the triage skill set the key. |
| R7 | NOTES_MISMATCH | a kept note in the window says won / lost / signed / cancelled / contract signed while `status = open` | `flag_notes_stage_mismatch` | Data-quality flag. The brief reports it; only `deal-triage` may propose the fix. |

**At-risk override.** R1 or R2 set → the deal is labelled **at risk** in the ranked list
whatever its priority. A hot deal with no booked next step is an at-risk deal, not a hot
one.

## Ranking (engagement-led)

```
priority = w_v · sqrt(value / max_value)
         + w_p · p
         + w_d · stage_depth
         + w_n · min(days_silent / 90, 1)
```

- Weights from `config.ranking`; default 0.35 / 0.25 / 0.20 / 0.20 (a presentation
  default — printed in every footer, tunable in plain words).
- `sqrt` on value so one large deal does not zero the rest. Value 0 scores 0 here and
  must earn its place on the other terms.
- `stage_depth = order_nr / max order_nr` within the deal's own pipeline, from Pipedrive's own `order_nr` only. A
  pipeline whose stages lack one is order-unknown: the term is omitted for it, R5, the speed-to-lead first-stage rule and the first-stage close-date review skip it, and the
  output carries one line per pipeline: "Stage order not available for <pipeline name>; stage-order rules skipped."
- `p` = the deal's own `probability`; else the stage `deal_probability` **if stage
  probabilities vary across the stages in scope**; else the term is omitted, the
  remaining weights are used as they are, and the footer says "probability not used —
  stage probabilities are not configured".
- Deals below `value_floor` are not ranked (still counted in aggregates).
- Two deals within 0.02 are a tie; order by value.

**speed_to_lead:** untouched leads (`next_activity_id` null and `was_seen` false) and
first-stage deals with zero activities rank above everything, newest first; the rest as
engagement-led. **hybrid:** speed-to-lead for the Leads inbox and the first stage;
engagement-led from the second stage on.

## Clusters and shells (one line each, never ranked as deals)

- **Import cluster:** ≥ 3 deals with the same `add_time` to the minute, the same stage,
  and zero activities.
- **Integration shell:** `origin` or `channel_id` names an app or API client **and** the
  deal has no person, no organisation, `notes_count = 0` and `activities_count = 0`.
  All four conditions; records created through an integration that were then worked are
  ordinary deals.

## Notes hygiene (applied before R7 and the buying-signal chip)

Strip HTML tags and entities. Drop: system notes ("This deal was converted…", "Deal
created from lead…"), notes that are only an @-mention, image-only notes, and notes
whose text repeats another note on the same record within 10 minutes.

## Buying-signal language registry v1 (evidence chip only — never scored)

Shown as `▲ buying signal: "<matched phrase>" (note <date>)` on a ranked deal when a kept
note contains buyer-side commercial language. Case-insensitive substring match on the
stripped note text.

Accepted launch languages are en-US, en-GB, en-AU, de, fr, it, nl, es, es-419, pt-BR,
pl, uk, cs, et, lv, fi, sv, no, ru, ja, zh-TW, ko, id, tr, and th.

The maintained phrase pack is currently English only and is shared by en-US, en-GB,
and en-AU. It preserves the pre-existing v0 English evidence terms:

budget confirmed · budget approved · budget signed off · quote accepted · accepted the
quote · accepted the proposal · signed · contract signed · ready to sign · willing to
buy · wants to sign · go-ahead · green light · PO raised · purchase order · legal
review · sent to legal · procurement · looped in procurement · start date · kick-off
date · verbal yes · verbal agreement · chose us · selected us

Every other accepted launch language currently has **no maintained phrase pack**. Its
buying-signal result is unavailable, with no inferred absent or negative conclusion. Do
not translate these phrases or use a model to infer equivalents until Sales Ops supplies a
reviewed pack and evidence source. A language outside the launch list is unavailable too.

A buying signal on a deal with no next step is at risk, not hot — say so.

## Window widening

Look-back for activity and note signals is 90 days. If it yields fewer than 5 human
touches (done activities on open deals plus kept notes), re-run the activities and notes
fetch once at 12 months and state which window was used.
