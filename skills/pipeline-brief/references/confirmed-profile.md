# Confirmed-profile and saved-evidence details

These rules sat in SKILL.md until release 0.7.0 moved them here to keep the body under 300 lines; the wording is unchanged. Read this file before a setup check, a requested brief or its delivery, a `describe` step, a visual artifact, or when dating saved evidence.

## Setup check

For a requested brief, dashboard or Doc with missing setup, use `workflow.py setup-check`.
Explain the required details, offer Complete setup or Skip for now, and ask one
question at a time. Reuse accepted account details and scope. Skipping leaves
settings unchanged; show the available text and its specific limitations.

## A requested version-two brief

When the user requests a version-two brief, preserve the saved computed Markdown. Explain
loaded coverage using names and verified links; never append owner or pipeline IDs.
Keep record links when repeating names in explanations. Follow the shared workflow guide
for local delivery and proposed schedules, including unavailable host capabilities.

## Describe and checked output

Before a new rep-facing report, use the guide's `describe` step for short, evidence-bound
situation summaries and one specific next move per selected deal. Keep source excerpts
and date meanings in expandable details. Preserve the tested charts and supported layout
even when one risk feature is unavailable. After the checked report, present the pending
output choice through the host question tool; question cards are separate from its text.
Use the guide's automatic evidence reuse; explicit fresh and scheduled scans bypass it.

## Visual artifact and setup discovery

When Visual artifact is selected, read `references/visual-artifact-guide.md`.
Build a tailored view from the same saved report; keep the existing dashboard route.

For setup discovery, read `references/account-context-guide.md` and prefer the host’s
account-context tool when exposed. Save it in a separate setup run; reuse confirmed settings.

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
