# Deal Triage dashboard design

The Deal Triage dashboard is a deterministic, read-only view of one completed triage
result. It is never a second fetch, ranking, diagnosis, or CRM-write path.

## Delivery and data contract

- Render only `./.pipedrive-sales/results/deal-triage-full.json` after an interactive
  result with R1 and R2 evidence available.
- Resolve observed client capabilities before offering a surface. Unknown clients have no
  dashboard offer.
- Native and local HTML use the same canonical renderer output. Native creation is
  host-mediated; local output is the visible `./deal-triage-YYYY-MM-DD.html` path.
- Never silently fall back from native creation. Keep the text triage and require a new,
  explicit local-HTML selection after host failure.
- Rendering makes zero Pipedrive calls, recomputes nothing, and has no controls that write.

## Content and safety

Keep the computed header, risk strip, role summary, ranked queue, supporting findings,
partial/unavailable disclosures, coverage line, and run log visible. Escape and format
computed values only; do not invent a cause, recommendation, count, owner, or evidence.

The dashboard can direct users to review a numbered item in chat. Chat alone handles
`accept <item>` and CRM writes. A manager never creates a task or any other activity for a rep:
there is no cross-owner write. The manager view shows a copyable, owner-addressed task brief with
no create action, for the manager to create in Pipedrive or paste to the owner.

The producer supplies separate brief fields; the type is `task` and the subject and note explicitly
say internal/coaching. The manager view uses them, while the original buyer-facing proposal
remains visibly labeled as the computed proposal.

Lead proposals retain their original item numbers after the displayed deal queue. Risk
counts describe shown deals; the proposal total includes lead proposals. Escalations count
computed proposals requiring a separate status, value, or close-date confirmation.

The local CLI accepts `--input`, `--output`, `--role`, and `--capabilities` (a JSON file of
resolved observed capabilities). It checks delivery eligibility before writing and prints
the actual output path plus the computed coverage line. The visual checker uses this CLI.

## Presentation

Use the Pipedrive green, deep green, mint, paper, risk red, and warning amber tokens;
semantic landmarks; system fonts; responsive 390px layout; and print CSS. Keep HTML
self-contained with no remote assets, CDNs, gradients, animations, forms, buttons, or
write-capable controls.
