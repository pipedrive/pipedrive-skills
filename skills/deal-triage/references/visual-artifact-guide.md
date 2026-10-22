# A visual artifact from the saved report

A visual artifact is a separate, tailored view of the same verified report. A
dashboard uses the skill's established charts and layout. A visual artifact can
use a stage board, deal blocks, a timeline or another visual that helps this user.
Both may be published through Claude's Artifact host; the choice is about the view.
Claude Doc remains the document option. “Both” means Dashboard and Claude Doc. “All three” creates the Dashboard,
Claude Doc and a separate Visual artifact from the same saved report. Honor a
previous explicit selection without asking again; the user can request another
combination in their own words.

Offer Visual artifact whenever a supported HTML/artifact route is available. If
only local HTML is available, say that it will be a local interactive file before
creating it. Do not promise a published Claude link without the actual host tool.
If a question tool allows fewer choices than the output list, use the guide's
numbered fallback or split the choices without dropping Visual artifact.

## Keep the figures and records fixed

Complete and save the computed report before designing the view. Read that JSON
and the existing chart data from the same snapshot; do not fetch the deals again.
Never copy figures from an earlier chat summary or calculate the brief by hand.
Any additional grouping must be computed in code from these saved records, with
its source fields and result retained for review. Keep currencies separate.

Use the report's verified record names and URLs. Deal names in tiles, details and
notes link to their own Pipedrive records. The same rule applies to people,
organizations and activities. Keep identifiers inside data and URLs only. Do not
write “deal 16”, “deals 51–53”, or shorten duplicate titles into an ID list. Show
duplicate deals as separate linked names, distinguished by their recorded stage,
value or close date when needed. Unknown links remain explicitly unavailable.

Carry the exact selected scope, excluded pipelines, observation date, coverage
and disabled/unavailable checks into the view. A partial scan remains partial.
Do not turn missing activity history into buyer silence or an at-risk colour.
No-next-activity can be shown as a recorded booking gap when the field was read;
if its risk rule is off, do not call it a computed risk flag.

## Let the design fit the question

For a pipeline stage board, give each stage its own column, each deal a linked
tile, and a detail panel with value, close date, next activity and recorded context.
Use the saved stage counts and values. A selected deal should remain easy to find
with keyboard focus and on a phone. Explain block sizing: a minimum height plus
a value-based increment is not strictly proportional to value. Closing-soon and
booking-gap markings need a readable legend and must use saved facts.

Use local/system fonts or supplied local font assets. Follow the existing
dashboard policy for no remote scripts, fonts, analytics or data calls. Opening
the artifact performs no CRM write; proposed changes remain in chat.

## Verify before delivery

Retain the HTML beside the report. Render it with the available browser/artifact
preview and inspect desktop and mobile views, links, keyboard selection and details.
Compare every displayed total, stage subtotal and deal field with the saved report
or retained grouping calculation. Check visible text for record IDs and unlinked
names using `record_presentation.check_text` with the report's `record_refs`; for
JavaScript views, check the rendered DOM, including opened detail panels, not only
the HTML source. This does not replace the numeric and source review.

After publishing through the Artifact host, run `scripts/artifact_delivery.py verify-publish` and send the line it prints (`references/presentation-guide.md`, delivery proof); the plugin hook saved the host's response, so there is nothing to save by hand.

If rendering is unavailable, explain that the file has not been visually verified.
Do not claim it passed a visual check. Preserve that limitation in the receipt.
Use the existing Artifact host success/read-back checks before saying it was
published. A Claude Doc uses its separate native creation and read-back flow, verified by `scripts/artifact_delivery.py verify-doc` (`references/presentation-guide.md`).
Keep the published response, verified URL and local file hash with this run.

The tailored artifact does not replace the canonical report or its charts. Creating
it, creating a Doc or viewing either grants no CRM write permission. Complete the
checked delivery reply after publication verification; never advance a checkpoint
on an unverified output.
