# The shared fetch contract

How the report skills (Pipeline Brief, Deal Triage, Kitchen) read Pipedrive for a legacy-path report: when the run starts, how open deals are paged against the response-size policy, what happens on an error or a 429, and what the hook does and does not capture. Each skill's SKILL.md names its own calls and page sizes; this file holds the parts they share.

## Before the first read

Start the run with `run_capture.py start` before the first read, so the hook's captures belong to it.

`python3 <skill folder>/scripts/run_capture.py start --run ./.pipedrive-sales/runs/<name>.json --today <YYYY-MM-DD>`

Name the run file that way, `./.pipedrive-sales/runs/<name>.json` with the leading `./`, in `start --run` and in `absorb --run`.

## Errors

The hook does not capture an error result; record a limit, 429 or error response in the run file yourself, so the script can name it.

## Open-deal paging and the response-size policy

2. **Open deals, oldest-updated first:** first call `getDeals(status="open", pipeline_id |
   filter_id, owner_id, limit=1, sort_by="update_time", sort_direction="asc",
   include_fields=[next_activity_id, last_activity_id, activities_count,
   undone_activities_count, email_messages_count, last_incoming_mail_time,
   last_outgoing_mail_time, participants_count, notes_count])`. With more than one pipeline in `scope.pipeline_ids`, send the probe and every ordinary open-deals page once per pipeline, each with that `pipeline_id`; never an unfiltered open-deals read. Save it, run compute.py
   `--pass 1`, and use its `suggested_page_size` (capped at 100 deals) for the first ordinary page. The probe's
   `next_cursor` is not the start of that ordinary page: request the ordinary page
   without `cursor` so its first record is not skipped. Run that
   pass-1 result before **any** ordinary page, movement, activity, lead, or notes call. If
   it reports `integrity_stop` or unavailable required stage data, return that result
   immediately; do not make downstream reads. The measured
   policy is: **Pipedrive response size** — bytes in one MCP response; **AI context
   reserve** — model capacity kept for validation, evidence, and rendering; and
   **Pipedrive account allowance** — a server-provided account limit, shown only when the
   MCP exposes it. Cap one response at 100 KB (102,400 bytes) by default and keep 15,000
   tokens for the AI context reserve. A team may raise only
   `config.fetch.max_response_bytes` after testing its client with a larger response.
   Fetch further pages with the same fields; recalculate after every saved page and use the new
   `suggested_page_size`. It uses the largest observed UTF-8 row with response headroom,
   not an average or a guarantee about unread rows. A recommendation of zero stops the scan. Stop before the reserve is consumed. When coverage is partial, keep the measured
   response-size and reserve diagnostics in the saved result; include an account allowance
   only when the MCP supplied it. If the first bounded ordinary page still has
   a `next_cursor`, treat it as the partial-coverage boundary unless pass 1 explicitly says
   another page fits inside the remaining reserve. Do not probe that cursor merely to find
   out whether it fits. If a page is unexpectedly large or parked in
   an unreadable file, retry the same cursor once with fewer deals; do not advance the
   cursor. If that retry fails, stop with partial coverage. **If the last page you fetched
   still carries a `next_cursor` you did not follow, coverage is partial** — name the
   unread records by sort order and never present the fetched set as the whole pipeline.
   After presenting a partial result, offer a further bounded scan of unread pages using
   the last unread cursor with the same scope, sort order and fields, or a narrower
   pipeline/saved filter. Do not guess the total unread count from a cursor. If the user
   chooses to continue, append exact new responses to the saved run, recompute, and
   preserve the partial warning until the final cursor is absent. In a new model context,
   size each page from the currently available context rather than claiming that the
   saved run's cumulative token estimate is the current context usage. Do not reuse a
   cursor if scope, fields, or sort order changed; start a new scan instead.
   If `include_fields` returns a validation error, mark dependent rules unavailable; do
   not split rule inputs across reads merely to make a page smaller. When a saved
   `response_file` cannot be read, record its path, run the final compute pass immediately,
   and paste its partial result; do not inspect scripts, capability files, or the sandbox to
   work around the unavailable page.
   Do not inspect helper source, generate placeholder custom-field values, or manufacture
   a replacement response body. A complete inline JSON body may be retained unchanged
   and checked using the capture guide. If the one-record body cannot be retained
   accurately, use its visible size only to choose one conservative ordinary page of at
   most two deals. If the client did not expose an exact byte count, say `Pipedrive
   response size: not measured exactly`; do not present a guess in KB as a measurement.
   Say `AI context reserve: 15,000 tokens` separately. If that ordinary page also cannot
   be saved, stop with partial coverage and name the unread records. Do not calculate
   deal totals, rankings, or advice from responses that were not saved in the run file.

## 429 recovery (D2)

**429 recovery (D2):** An allowance-exhausted or malformed/unknown 429 never retries and
never requests another page. Preserve server guidance verbatim when present. A throttle
may be retried exactly once only when all of the following are already verified: the MCP
has declared the retry-time field authoritative, the response classifies it as a short
positive retry time, and the current client capability record explicitly sets `wait` to
`true`. Treat the raw field as declared authoritative only when the 429 body contains
`retry_after_seconds_authoritative: true` alongside a positive `retry_after_seconds`; a
bare retry-time field and any prose claim remain unknown. Wait the server-provided time,
repeat the identical optional request once, then
stop enrichment regardless of that retry's result. The MCP field contract and client wait
capability are not yet validated, so the shipped/default capability is `wait: false` and
every live 429 currently follows the unknown safe-stop path. Do not infer a retry time
from prose, a generic error, a local budget, or an account allowance. A local
`.pipedrive-sales/client-capabilities.json` file is not a shipped client profile and
cannot enable a retry. If the user supplied that local file as the reason to wait,
state in the final brief that its wait claim was unvalidated, the optional notes read
was not retried, and notes-dependent evidence remains unavailable.

## The next page's parameters come from absorb (0.7.14)

After every saved page, `run_capture.py absorb --run <run>` saves the parameters of the next page of the run's last unfinished read to `./.pipedrive-sales/runs/next-page-params.json` and prints them on one line. The next call of that tool uses exactly those parameters, copied from that output: never a cursor or offset from memory, from an earlier message or read out of a response file (0.7.13 run (c): a retyped cursor cost one wasted call and a hand edit of the run record). When absorb prints "no further page", the chain is complete and no read with a cursor follows. The plugin hook flags a page whose cursor or offset is not the saved one, absorb leaves that page out of the run (`excluded_calls`) and says so, and the retry uses the saved parameters. The run record is never edited by hand: a call to leave out for any other reason is recorded with `run_capture.py exclude --run <run> --tool-use-id <id> --reason <why>`.
