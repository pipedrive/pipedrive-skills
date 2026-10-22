# Dashboards and Claude Docs

Read this after a completed interactive result or when the user requests an output for a saved result. Scheduled runs remain text-only. Reuse the complete saved JSON; output creation never fetches CRM data or authorizes CRM writes.

## Output choice

After every completed interactive run eligible for an output offer, read `references/question-guide.md` and present **Dashboard / Claude Doc / Visual artifact / Both / All three** plus Nothing else through the current host's selectable question tool when available. Use its concise text fallback otherwise. Ask output first and dashboard style only if a dashboard was selected; do not combine unrelated choices into a long paragraph. If the user already explicitly requested an output, honor it without asking again. A user may decline and keep the chat report. If they ask to keep a saved text report usable, paste its saved `markdown` and coverage line in the response. Tell them that the decline applies only to this conversation and was not saved as a future opt-out. Confirm the selected destinations only when the current host has more than one supported route.

Check the current tool catalog for native Claude Docs support before promising a document. If it is absent, explain that native Claude Docs is unavailable here; offer a separately chosen Markdown/PDF export instead. Never call a Markdown file or PDF a Claude Doc. If the user chooses Both or All three and one route is unavailable, explain that and ask which available outputs they want; do not silently substitute.

## Design choice for dashboards

Default to **Combined**. Explain the available choices in ordinary language:

- **Combined (recommended):** Claude arranges tested charts and report sections. Numbers, labels and interactions stay consistent. Chart structure is fixed, so there is less freedom to redesign each chart.
- **Claude designs the dashboard:** Claude can tailor the whole layout and charts. The design can vary between runs and needs extra review for missing labels, working interactions and accurate displayed facts.

For an explicit dashboard request, say “I’ll use Combined unless you prefer Claude to design the whole dashboard” and continue with Combined unless a different preference is already given. After an output-offer selection, use the short style question in `references/question-guide.md`. A bare output approval never selects the custom mode. Honor a previously stated design preference in this conversation.

## Branding and chart requirements

Refer to every CRM record by its saved name and hyperlink that name to the verified Pipedrive record: deal title, activity subject, person name, organization name, lead title, or other record name. Never display numeric record IDs or append “Deal #…”, “Activity #…” or “Contact #…” in dashboards, Claude Docs, or document exports. IDs remain in structured evidence, URLs and tool parameters. For duplicate names, retain the exact underlying record link and use verified organization, owner or date context if needed; never guess a match from the name alone. If the saved data lacks a name or verified record URL, state that specific limitation without inventing either. Acquire display names and links during the original evidence collection; output-only requests make no additional CRM reads. A link to a parent deal is not proof of a direct activity or note link.

Use a light background and white cards. Green leads: #017737, dark green #014722, light green #D7F9D3, white #FFFFFF. Purple supports: #6861F2, dark purple #3B3A97, light purple #CFD0F9. Purple must not dominate the composition. Yellow #FFDE00 is reserved for call-to-action elements; do not use brand yellow for ordinary bars or warnings. Neutral and error tokens are semantic UI colors, not additional official brand swatches. Use the packaged typography sizes and system-font fallback; do not claim an official font family has been verified.

Required charts:
- Brief: stage values and counts, rule counts, value versus CRM activity recency.
- Forecast: stacked activity-recency values, value/recency scatter, grouped monthly open-value versus likely-revenue bars. Manager commit is a separate entered judgment, not a stage bucket. Never invent Commit/Best Case/Pipeline deal categories to imitate an example screenshot.
- Triage: rule counts, value/recency scatter, detailed numbered action cards and execution status.

Each chart needs visible numeric axes or scales, currency/count/day units, a legend when groups are present, hover and keyboard details, touch-accessible details, and a supporting data table. Separate currencies. Retain unknown-date exclusions and partial coverage. Sparse selected-deal reviews stay sparse; do not invent points. Preserve both activity dates and their explanation: scheduled means planned event date; marked done means the timestamp Pipedrive recorded completion. The scheduled date of a completed activity is the recency proxy, with a labelled completion-date fallback if missing. Neither proves buyer contact. Preserve all original activity-date evidence in saved JSON and include both dates in chart detail tables and documents.

## Combined builder

The full completed result is `./.pipedrive-sales/results/<skill>-full.json`: the compute script's `--output` flag wrote it (brief, triage, forecast; sales-outreach `result --output`; meeting-follow-up `propose`), or `workflow.py report --output` did on the confirmed-profile path. Never write it by shell redirect, and never run a compute a second time for it. For Triage, use the latest execution-enriched saved result, retaining original item numbers and proposals. Prepare tested components:

```bash
python3 <skill folder>/scripts/dashboard_builder.py prepare \
  --input <saved-full-result.json> --output <new-bundle.json>
```

For Triage, also pass `--role <rep-or-manager>` and `--capabilities <resolved-capabilities.json>` so manager controls keep their existing restrictions.

Prepare prints a compact summary of the component headings, placeholders and `default_layout`.
Use it to compose the layout without loading the entire bundle into context. Read a
particular component only if needed. The layout is the bundle's `default_layout`: run assemble with `--layout default` (0.7.19 decision 7A: the same section order every run, so two runs compare). Only when the rep asked for a different arrangement does Claude write a layout fragment with every `{{pd:N}}` placeholder exactly once, arranging sections in appropriate grids or containers. A currency section (its heading is the currency code: EUR, GBP) takes a full row of its own; never put two currency sections side by side. Their five figures and two chart columns need the width, and a half-width row broke the figures mid-number in the 0.7.11 release run. Rows of two suit the concentration risks and the triage section. Add layout CSS and descriptive headings if useful. Preserve the supplied components; do not recompute facts or invent factual headlines. Keep IDs and interactions intact. Do not hide components, load external resources or add scripts.

```bash
python3 <skill folder>/scripts/dashboard_builder.py assemble \
  --bundle <bundle.json> --layout <layout-fragment.html> --output <new-dashboard.html>
```

Use the builder's returned SHA-256 for delivery verification. The packaged renderer remains a tested component source and preview, rather than the mandatory final layout. Do not silently substitute the packaged layout if composition fails; repair the layout or report failure.

## Optional Claude-designed mode

Only after the user selects it, let Claude design a self-contained HTML artifact from the bundle's `facts` and the requirements above. It may redesign charts and sections. It must preserve computed amounts, scope, dates, named deals, action states and limitations. Charts must use the saved data and disclose any exclusions. No external code, assets or CRM operations.

Review the actual visible candidate at desktop and mobile sizes. Check chart totals/data against saved JSON; inspect units, legends, focus/hover/touch details, branding and action state. Save a review JSON containing the bundle's `source_sha256`, the candidate's exact `html_sha256`, and booleans `facts`, `coverage`, `dates`, `action_states`, `charts`, `tooltips`, `branding`, `mobile`. Set each true only after that check passes. These are recorded review findings, not a claim the script can prove visual or semantic correctness.

```bash
python3 <skill folder>/scripts/dashboard_builder.py validate-custom \
  --bundle <bundle.json> --html <candidate.html> --review <review.json> \
  --output <new-validated-dashboard.html>
```

The check rejects mismatched source/HTML hashes, incomplete reviews and obvious external references; it includes the activity date meanings without adding a global calculation section. Use the returned output/hash for publication. Do not claim automatic validation of all displayed facts.

## Delivery proof

Before either dashboard mode, run `scripts/artifact_delivery.py profile --client <client> --artifact-tool <yes|no> [--local-folder <connected folder>]` and pass its output to `delivery_options(result, capabilities)` and to `dashboard_builder.py prepare --capabilities`; without it, or with no delivery option in it, prepare builds nothing. Report only what this session shows: `--artifact-tool yes` when an Artifact publish tool is available to you now, and `--local-folder` with the path of the user's connected working folder, if there is one. The script takes how the host publishes (supplied HTML unchanged, the storage notice) from the adjacent validated `client_capabilities.json` and tests the folder itself; do not judge those yourself and do not write a probe file. The Artifact tool's page wrapper does not change this. Core unavailable evidence keeps dashboards blocked. Unknown/unobserved delivery routes remain text-only; do not claim the client itself cannot create Artifacts.

On claude.ai, `--artifact-tool yes` needs code execution and Artifact file-path publication; cloud output files are not a connected folder.

When multiple surfaces are validated, use the selectable destination question from `references/question-guide.md` and require an explicit choice: Claude dashboard (account-stored; give the validated storage notice) or Private local HTML (beside working files). If only one is available, explain that destination. A request naming it already selects it.

Choose an absent output path. For local delivery use `verify-local --exit-code <builder-exit> --path <output> --sha256 <builder-hash> --absent-before`. For native delivery publish the exact validated builder file once with the Artifact tool, then run `scripts/artifact_delivery.py verify-publish` with no other step: the plugin hook saved the host's response (`.pipedrive-sales/runs/artifact-publish.json`), the script verifies it and prints the one line to send; send the text between its marker lines as the reply (or, when it says a checked reply saved earlier comes first, after that reply). Never write the verification line yourself. Only on a host without plugin hooks (claude.ai chat) save the complete unmodified host response to a file and run `verify-native --host-result <saved-host-response>`. On claude.ai put it under `/mnt/user-data/outputs/`, verify bytes/hash, then use the observed Artifact file-path publication tool. Do not reconstruct publication evidence from a link/card, add a second copy to retry verification, or silently switch destinations; never silently fall back. If raw evidence is inaccessible, report visible publication with incomplete automated verification. Include the saved result's coverage and snapshot date with the verified link/path.

## Triage execution refresh

After each confirmation turn, keep the original full scan JSON and original item numbers.
Append each write's exact params and raw response to the run, along with `accepted_items`
and `skipped_items`. Run this only to build a new enriched copy for dashboard or document creation, without recalculating or reranking.
It does not update the saved result: each confirmed write already records its state there (`write_confirmation.py --result`), and the refresh keeps any recorded state (written, failed or uncertain) over a pending or accepted state from the run:

```bash
python3 <skill folder>/scripts/triage_execution.py \
  --result <original-full-triage.json> --run <updated-run.json> \
  --output <new-execution-enriched-result.json>
```

Use the new result for dashboard/document creation. `action_states` maps original item
numbers to pending, accepted, written, skipped or failed; only matching successful saved
write responses establish written status and Activity IDs. Historical flags remain scan
findings. A completed/accepted/skipped/failed item has no live accept-copy control.
Before another attempt, freshly verify the current records and obtain the applicable
item confirmation. A successful written item is never retried just because its original
scan still had a no-next-step flag.

## Native Claude Docs

Use the current host's Claude Docs guide/schema before creating a document; do not guess tool signatures. Create one native editable document from the same completed saved result, after the user selects Claude Doc, Both or All three. First run `scripts/artifact_delivery.py doc-outline --input <saved result>`: it prints the doc's title and headings (0.7.19 decision 7A: the same title and outline every run), and `verify-doc` holds the created doc to that title. Create the doc with exactly that title and those headings in that order, then fill each section from the saved result. Include summary, scope/snapshot, named findings and next steps, calculated figures separated by currency, both activity dates with their meanings, partial coverage/evidence limits, and evidence inside each needs-attention card. Do not add a global “How this was calculated” section to chat, dashboards, or Claude Docs; keep the full audit in saved JSON. State unresolved activity dates as unresolved, not as proof that no completed activity exists. Triage must show pending/accepted/written/skipped/failed status and linked activity subjects; creating a document never accepts a proposal. Preserve risk signals without turning them into unsupported predictions or claiming a scheduled activity proves buyer contact. Extra recommendations must be labelled as recommendations.

Use native create/update tools as documented by this session. After the last fill, read the doc back with the Claude Docs read tool (its body node, kind `view` or `outline`), then run `scripts/artifact_delivery.py verify-doc` with no other step: the plugin hook saved the connector's create, fills and read-back (`.pipedrive-sales/runs/claude-doc.json`), the script verifies them and prints the one line to send (`Claude Doc created and read back: <link>`); send the text between its marker lines as the reply, after the dashboard line when both were made this turn (the script prints them together), or after a checked reply saved earlier this turn when it says so. Never write that line yourself; when the script says the doc is not read back yet, read it back and run it again. Only on a host without plugin hooks (claude.ai chat) verify with the returned document link and your own read-back. A local file or Artifact HTML is not native document proof. If creation fails, report failure and retain the chat result; offer local exports only as an explicit alternative. Never publish or share outside the user's chosen destination.

For separately selected local exports, `scripts/report_export.py --input <saved-result> --output <new-path>.md` (or `.pdf`) uses the same saved facts. Local file access needs a current-session write/read probe; PDF also needs a working Playwright Chromium probe. Verify absent path and nonempty read-back. Do not substitute it for Claude Docs without the user's choice.

## Durable run evidence and independent checkpoints

For each run, preserve the dated raw run JSON, every referenced response file, and the full computed result on the user's working filesystem. Preserve each execution refresh and builder bundle/layout/output, returned SHA-256, and unmodified publication response there too. Do not leave the only copy in the Cowork sandbox or overwrite earlier results. Record observed source times separately from reuse times.

Use `config.last_runs["pipeline-brief"]`, `config.last_runs["deal-triage"]` and `config.last_runs["forecast"]` independently. The legacy `last_run` is a frozen migration baseline; never update it. On first completion, the helper preserves that baseline for all three skills before advancing only the completed one. Never retrospectively change the existing baseline or claim its originating skill is known.

After setup is verified, the scope and required evidence are complete, and every selected output has been delivered and verified (or the user explicitly declines output), retain the evidence and advance only this skill's checkpoint:

```bash
python3 <skill folder>/scripts/run_state.py --config <config-path> \
  --skill <pipeline-brief-or-deal-triage-or-forecast> --run <dated-raw-run.json> \
  --result <dated-full-result.json> --evidence-dir <working-folder>/.pipedrive-sales/evidence \
  --outputs-complete --setup-verified
```

Pass each selected output file, builder bundle/layout and raw publication/document response as a repeated `--attachment <path>` argument. The archive retains original bytes and includes a replay run with response paths resolved into the archive.

The flags attest to checks actually performed; never set them while an output fails or an output choice is pending. Verify the returned checkpoint and evidence path. Partial reads and failed outputs retain the previous checkpoint; explain this and the next movement window. During triage confirmations retain responses and execution states immediately, but advance its scan checkpoint only after completing this delivery checkpoint. Persist triage statistics once per scan, replacing its cumulative daily entry rather than adding the same cumulative counts on every confirmation.

## Document consistency checks

Derive current action counts and claims from execution state. Say four skipped deals have no next activity and one has an overdue call when that is what the evidence shows; never turn that into five deals with no step. Label created, updated and observed write timestamps separately. Unknown write time stays unknown.

For every named deal in a document, include the scheduled date, marked-done timestamp and the source used for recency, or explicitly say not recorded. If the source is deal update or inbound email, describe that actual source rather than calling it a completed activity. Activity evidence dates are independent of execution timestamps.

Every embedded chart must have an adjacent text/table summary of its figures, units, exclusions and scope so the exported document remains useful without the widget. Retain tables and date explanations in both native Docs and chosen exports. A missed close date does not automatically put a deal into another forecast period: inclusion changes only when its expected close date is updated into that period. Label advice as a recommendation.

## Deal hyperlinks in every selected output

The completed result's `output_domain` is the shared, confirmed company domain. Preserve its computed Markdown links in chat and every document. For native Claude Docs, create actual hyperlink marks using the host's documented document schema; a plain name, #ID or printed URL is not a hyperlink. If you rewrite a deal title or shorten its label, keep its corresponding `https://<domain>.pipedrive.com/deal/<id>` target. Link every named deal supported by the saved result, including closed deals. Never link a lead as a deal or guess an ID or company host.

After native document creation, read it back and check that the named deals carry link targets, rather than just text. Fix missing hyperlink marks before claiming the document is linked. Markdown/HTML/PDF exports preserve the same links through `scripts/report_export.py`. The standard dashboard renderers and chart data tables supply links automatically. Combined layouts retain those anchors; Claude-designed dashboards must keep the same allowed deal links and pass the existing resource checks. Links open Pipedrive only when clicked; do not remove offline resource restrictions.

When no domain was supplied, keep plain labels and explain that company links were skipped for this run. Do not ask again during output delivery or invent a domain.

Keep the same featured deal order and next steps across chat, Dashboard, Claude Doc and Visual artifact. Use the saved priority order; do not quietly promote a different deal because it has a nearer close date. Any explicitly requested alternative ordering needs its stated reason. When a next activity already exists, review that activity before recommending another booking. Name and link activity subjects in evidence tables using verified source links; a deal link is not an activity link. Keep creation, scheduled and marked-done dates separate, and apply the accepted timezone.
