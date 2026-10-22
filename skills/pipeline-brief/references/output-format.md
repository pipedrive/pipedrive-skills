# Pipeline Brief output format

The saved full result is the source for both chat and the optional dashboard. Show the
computed `markdown` without rewriting its numbers, deal names, actions, or coverage.

## What a rep sees first

1. How many open deals were checked, and a plain warning if more remain unread.
2. The included sales scope, with pipeline IDs only when two pipelines share a name.
3. Open value and any relevant new leads.
4. Up to five named next actions. Each action says what to do and why. Distinguish a
   confirmed customer touch from an estimate based on record updates.
5. Movement and close dates only where the saved result supports those claims. When
   named closing or movement items are saved, show a short named list in chat as well
   as the dashboard. Keep each deal's currency attached to its value.
6. When the settings list `priority_fields`, each named deal also carries one line,
   `Fields you track: <label>: <value> · …`, with the values the connector returned. A field
   with no returned value is left out, and with none set the deal line is unchanged. These
   fields never change the order or filter a deal.

Do not present a fetched subset as the entire pipeline. For a partial read, say that the
totals and recommendations cover the checked records and that recent updates or deals
closing this week may be missing. The first screen should make sense without a rule code.

## Saved calculation evidence

Keep coverage, rule IDs, evidence grades, thresholds, default ranking, fetch limits, and
the footer in the saved result for review. Do not add a run-level "How this was calculated"
section to chat, the dashboard, or a Claude Doc. Keep each named deal's plain-language
reason and next step in its Needs Attention card. Never invent buyer contact from a
record-update proxy. A partial scan still needs a prominent checked-record boundary and
an offer to continue in bounded batches or narrow the scope.

## Scheduled and unavailable results

Scheduled output is shorter and does not offer a dashboard. If required contact evidence
is unavailable, show the text result and its limitation without claiming a visual was made.
An empty pipeline or a small pipeline should still give the most useful supported next
step. No delivery surface makes an additional CRM read.

## Optional dashboard

Offer only surfaces enabled by the packaged versioned client profile and any required
current-session probe. State the storage destination before the user chooses. Render from
the saved full result and claim success only after the selected local file or native host
response passes creation proof. Preserve the plain checked-record boundary in the response.
