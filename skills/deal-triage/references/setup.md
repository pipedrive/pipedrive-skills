# Setup questions — read ONLY when config keys are missing

Each question: plain language, one sentence of explanation, a 'skip for now' answer. Write each answer to `.pipedrive-sales/config.json`.

Follow `references/account-context-guide.md` first when account context is available.
Use verified pipeline names in the scope question, with stage ranges as supporting
descriptions; keep IDs internal even when names collide. Preserve those names in saved `pipelines`.
Use authenticated `current_user.id` for “my deals”, without a notes identity probe.
Only use the stage/notes wording below when that account metadata is unavailable.
Read `references/company-url-guide.md` during setup: reuse a saved domain or valid
authenticated `company_domain`; ask only when neither is available. Save `output.domain`
with read-back and reuse it across all three skills. Preserve it when replacing settings.

## Shared questions (from pipeline-brief)

```
0. How does your team decide what to work on first?
   (a) warmest and biggest deals first — most engagement, highest value, latest stage;
   (b) newest first — brand-new leads or signups with no contact yet get called before
       anything else;
   (c) both — newest first for new leads, warmest first for deals already in progress.
   Explain: teams differ; a subscription seller calls new signups first, a project seller
   works the hottest deals first. The skill orders everything by this answer.
1. Scope. Show what was discovered: "I can see these pipelines — [stage names per
   pipeline]. These look like sales pipelines: … These don't: … Include all the sales
   ones?" Then: "I see deals owned by N people — [names from notes where available,
   otherwise IDs]. Which is you, and what should I call the others?" Include the Leads
   inbox? (defaults to yes for answers b and c above). Optional: "Do you already review
   your pipeline from a saved Pipedrive filter? If so, select it in Pipedrive and give me
   the number from the address bar — I'll use exactly that view. Make sure it's shared."
   Explain: nothing to look up; the skill reads what it can and asks only what it can't.
2. Silence threshold in days. Explain: "no reply in N days" is the most common early
   warning; suggest they pick what their sales cycle treats as too quiet.
3. Flag deals with no scheduled next activity? Explain: Pipedrive's own discipline is that
   every deal has a next Activity.
4. Days in a stage before it counts as stale. If Pipedrive rotting is on for a stage,
   propose its days-to-rotten and ask to confirm; otherwise ask for one default,
   per-stage optional.
5. Days of slack after expected close before flagging a slipped close.
6. Minimum deal value to appear in the ranked list. Should the ranking favour deal size,
   closeness to closing, or how long a deal has been quiet? (Default: balanced — the
   weights are shown in every brief and can be changed any time in plain words.)
7. Flag deals whose notes say won/lost/signed but the stage hasn't moved? (yes/no)
8. Output: let the brief pick its shape from the record count (default), or always short
   / always full; language; reporting currency.
Write `last_run` after each successful run.
```

## Additional questions for this skill

```
9. From which stage onward should a single linked contact be flagged? Explain:
   single-threaded deals go quiet more often; early stages are usually exempt.
10. Under-qualified deals: "In Pipedrive, build a filter for the deals you'd call
    under-qualified — for example 'Stage is Proposal or later AND Decision-maker is
    empty' — using the normal filter builder with your own field names. Include
    'Status is open'. Make the filter shared, select it, and give me the number from the
    address bar. Then tell me in one sentence what it checks — I'll show that sentence
    as the reason whenever it flags a deal." Repeat for any other rule they want as a
    filter (e.g. "no email in 14 days", "over 50k with no next step"). Explain: Pipedrive
    already has a rule builder everyone knows; the skill uses it rather than asking for
    field codes.
11. May this skill create Activities and notes in Pipedrive after you confirm them?
    (yes/no). Nothing is ever written without your confirmation, and a plain "yes" is
    never enough — you'll always say which item. Would you like to confirm one at a
    time, or review the whole list and confirm once?
12. Optional: what counts as "high probability" for your team, and how far out does a
    close date have to be before a confident deal looks parked? (HubSpot's defaults are
    70% and 60 days — offered, not applied.) Explain: a deal you believe in but have
    pushed two months out is usually worth a call to bring forward. Skipping leaves
    this rule off.
```

## Tester footer

Testers can set `"tester_footer": true` in `.pipedrive-sales/config.json` (off by default, version-1 and version-2 settings). Each result then ends with one line, `Tester: <skill> <version> · <N> calls · ~<T> tokens of Pipedrive data read by the scripts (estimated)`. Every saved result carries `skill`, `version`, `calls` and `estimated_tokens` whether or not the footer is on.
