# Confirmed-profile and saved-evidence details

These rules sat in SKILL.md until release 0.7.0 moved them here to keep the body under 300 lines; the wording is unchanged. Read this file before a setup check, a `describe` step, a visual artifact, or when dating saved evidence.

## Setup check

For requested deep triage or output with missing setup, use `workflow.py setup-check`.
Explain what is needed, offer Complete setup or Skip for now, and ask one required
question at a time. A skipped check stays off; context-only review grants no CRM
permission. Resolve pending item approval before unrelated setup or output questions.

## Describe and checked output

Use the guide's `describe` step to explain the situation and supported next move in
plain language while leaving exact proposal parameters unchanged. Keep due dates,
creation dates and completion timestamps distinct in source details. Retain the tested
charts and accepted thresholds. A selectable accept/edit/skip card can follow the checked
report; do not interrupt pending acceptance with unrelated output questions. Reuse saved
evidence for diagnosis, then perform actual fresh preflight reads before any approved write.

## Visual artifact and setup discovery

When Visual artifact is selected, read `references/visual-artifact-guide.md`.
Build a tailored view from the same saved report; keep the existing dashboard route.

For setup discovery, read `references/account-context-guide.md` and prefer the host’s
account-context tool when exposed. Save it in a separate setup run; reuse confirmed settings.

## Saved evidence and dates

For dashboard timestamps, use the verified `config.timezone` when present; otherwise
label the display UTC. Keep original timestamps in saved evidence, convert instants
with daylight-saving rules, and leave date-only fields unchanged.

Save tool responses unchanged with their parameters and observed timestamp; never rebuild
responses from older rows or patch selected fields to imitate a fresh read. Keep setup
calls in a separate discovery run. Cached evidence retains its original timestamp and is
not counted as a new call. Mark its freshness limits explicitly. A date rollover creates
an older snapshot, not a retroactive date error. Preserve completed activity scheduled
dates and marked-done timestamps separately using `scripts/activity_evidence.py`.
Explain which date supplies the recency proxy; neither proves buyer contact. Copy close
dates from computed items exactly. Risk flags suggest review; they do not prove a date
is a placeholder, a deal will slip, or an activity was buyer contact.
