# Keeping the original Pipedrive responses

Save the run JSON in the user's working folder, under `.pipedrive-sales/runs/`.

In the confirmed-profile workflow, run the next `workflow.py plan` before saving a
response. Where the plugin hook runs (Claude Code, Cowork), `plan` adds this run's
Pipedrive reads to the run itself and prints `captured automatically: N responses … do not
copy them`. Only after that line, skip copying those responses, and still save any read
`plan` lists again after you made it. Without that line (claude.ai chat, 0 captured, or a
workflow with no `plan` step), save every response as below.

The hook does not capture an error result; record a limit, 429 or error response in the run file yourself, so the script can name it. One exception: an oversized result the host replaces with a file pointer ("exceeds maximum allowed tokens. Output has been saved to …") is captured from that file by the hook (`capture: hook_file`, 0.7.12); a pointer the hook could not read goes into the run as a failed read when `plan` or `absorb` takes it, so the report names it. `--response-file` below also takes the host's pointer file as the original.

After each MCP response you save, retain its complete JSON before doing another page or publishing.
Preserve original bytes when the host exposes them. Complete inline JSON is also valid
compute input; lacking a wire-byte archive alone must not block a report or setup save.
This retention does not advance a last-run checkpoint and does not depend on output delivery.

When the host returns a JSON file, save the exact request parameters to a JSON file and run:

```bash
python3 scripts/response_capture.py --run <run.json> --response-file <original.json> --tool getDeals --params-file <request-params.json>
```

The helper copies the response into `runs/responses/` and appends the original request
and durable response path to the run. Preserve any actual `elapsed_ms` supplied by the
host; never invent timing. `--observed-at` may record an actual observation time.

When the host supplies an exported transcript, this preserves the original JSON text:

```bash
python3 scripts/response_capture.py --run <run.json> --transcript <host-export.json> --tool-use-id <original-tool-use-id>
```

Transcript capture requires one original tool request and one complete JSON text response
matched by tool-use ID. It does not scrape summaries, guess parameters, or reconstruct rows.
For a host that can pipe the actual raw JSON bytes, `--response-file -` reads stdin.
If the connector exposes only a complete inline JSON body, copy that entire body unchanged
to a JSON file in the working folder. Preserve every field, null, array, value and pagination
envelope; do not select rows, summarize, normalize, generate substitutes or fill omissions.
Save the exact request parameters separately, then use:

```bash
python3 scripts/response_capture.py --run <run.json> --inline-json <complete-inline.json> --tool getDeals --params-file <request-params.json>
```

This records `capture_kind: inline_json` and `original_bytes_retained: false`: the complete
connector JSON is retained, but original wire formatting cannot be verified. Read back
the saved response and compare row count, IDs, values, dates and pagination with the visible
response before computing. For setup, compare authenticated identity, pipeline names and
IDs, domain and timezone before saving metadata. A JSON parse alone does not prove a copy
is accurate. Stop only if the body is truncated, summarized, incomplete or cannot be
copied and checked accurately. Never rank from reconstructed rows or invent missing pages.
Do not search hidden transcript directories or request sandbox changes just because a
complete inline response is available. Do not refetch data merely to change its capture format.
The helper cannot recover responses already deleted by the host.

For an older run with readable temporary response files, copy every original page now:

```bash
python3 scripts/response_capture.py --run <run.json> --relocate
```

Keep the computed JSON result alongside this evidence, even if an artifact publish check
fails. Do not advance a checkpoint until the skill's existing completion checks pass.
