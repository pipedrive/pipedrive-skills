# Deal Triage output format

The saved `markdown` and `presentation` come from the same computed result. Show at most
ten flagged deals in priority order. Keep every verified finding, but lead with one
supported decision per deal.

## Rep view

1. Say how many open deals were checked and whether more remain unread.
2. Show the counts for acting now, reviewing existing tasks, and checking missing
   evidence, then the top three decisions and complete numbered queue. Count a
   proposed first-contact call for a lead under acting now.
3. For each deal, name the buyer or company, the next decision, its due date when known,
   and the reason in ordinary sales language.
4. Show an existing task as an existing task. Tell the rep to review or reword it in
   Pipedrive; do not propose a duplicate activity.
5. Say what evidence is missing when a signal is unknown. Unknown contact count does not
   mean zero contacts, and an incomplete activity read does not mean no next activity.
   Put this limit before the queue, even when no verified finding remains, and give the
   rep a concrete check to make before treating the deals as clear.

Keep the complete technical audit in the saved JSON. Put supporting evidence inside
each needs-attention card, using the rule's description instead of an unexplained ID.
Keep coverage limits and write outcomes visible. Do not add a global “How this was
calculated” section, or print `WHAT:`, `HOW:`, `Cause: <…>` or repeated evidence sentences.

## Confirmation and writes

The numbered proposal ID maps exactly to the numbered queue item. Only chat can accept
an item and use the existing write whitelist. Repeat the exact record, activity type,
subject, and due date before an item-specific confirmation. A bare “yes” to multiple
items writes nothing. A suggested change to a deal's date, value, or status is separate
from an activity proposal and needs its own confirmation.

## Dashboard

The dashboard is read-only. It uses the same decisions and evidence as chat and makes
no CRM call. Offer it only after an eligible interactive result with available core
evidence and a validated client surface. Claim delivery only after the selected local
file or native host response passes creation proof. Scheduled runs and blocked evidence
remain text-only.
