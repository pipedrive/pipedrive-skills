# Troubleshooting — pipeline-brief

| Symptom | Likely cause | What the skill says / does |
|---|---|---|
| Pipedrive tools not available | MCP not connected or token expired — two different error strings for the same condition ("requires re-authorization (token expired)" and "needs authentication") | "Connect or re-authorise the Pipedrive connector in your client's settings and retry." Do not guess data. In `mode=scheduled`, the account-context read catches this first and emits exactly one line. |
| Account context unavailable (tool absent, call fails, or no stages returned) | Older connector without `getAccountContext`, or an incomplete response. No stage tool exists, so there is no other stage source | "Pipedrive's account context is not available, so I have no stage data. Update the Pipedrive connector and retry." Do not guess stages, scope or stage order. |
| Tools present but every call fails with a permission error | Company admin has disabled MCP at company level | "Your Pipedrive admin has MCP switched off for the company — this is a Pipedrive setting, not an AI problem. Ask your admin to enable it." |
| Coverage below total | Token budget reached (every deal response carries ~40 fields) | Name the unread slice from the pages actually fetched. An ascending page followed by a descending page leaves the intervening update-time slice unread. Offer a saved Pipedrive filter for a smaller next brief. |
| Calls stop mid-run / quota error | MCP usage limit on the plan | Report calls used; offer a narrower scope; never retry in a loop. |
| A call returns a 5xx / "the connector's server returned an error" | Transient | Retry the identical call once. On a second failure name the call in the coverage line; never silently drop its data. |
| `include_fields` returns a validation error | Older deployment accepting one value only | Fall back to one `getDeals` call per field, merge on `id`; note the extra calls in the count. |
| `getLeads` rejects `sort` | The parameter takes a bare field name (`add_time`, `update_time`, `was_seen`, …), no direction | Use `sort="add_time"`; the results arrive oldest first — compute.py orders them. |
| `getDeals` rejects `status` | One status per call (`open`, `won`, `lost`, `deleted`) | Fetch won and lost as two calls. |
| Saved filter returns an error or nothing | Filter is private to another user, or its ID was mistyped | "This filter isn't shared with you — ask its owner to share it, or check the number in the address bar." Do not fall back to an unfiltered fetch without saying so. |
| Tool name not found | MCP updated tool names | Report the exact name; do not substitute a different tool. |
| Owner shows as a number | That user has never written a note in the window, so no name is available | "No name available for owner 12345678 from Pipedrive — tell me who this is and I'll remember it." |
| `scripts/compute.py` cannot run | No code execution in this client, or the first script command is refused in the plain form | Follow SKILL.md "No script available": compute in-model from the written rules, open with the fixed "Not code-checked" line, write "arithmetic: model" in the coverage line, and offer no dashboard, checkpoint, history entry or saved result. |

## Connector errors, missing tools and tool names

- **403 on a read:** stop that read. Say once: "Pipedrive refused that request. Reconnect the Pipedrive connector in your client settings and try again." Never retry it and never substitute another tool for it. In deal-triage the same applies to a write: report it as not written and keep the item as a proposal only.
- **A tool is missing after a tool search:** say "<tool> is not available in this workspace; ask your Pipedrive admin to re-add the connector." Name the tool, stop that step, and never guess its data or use a different tool in its place. Then name the checks that depend on that tool and say they were skipped: without `getNotes`, the notes-mismatch rule R7 and buying signals; without `getActivities`, the silence and overdue rules.
- **The context tool:** call whichever name is registered (`getAccountContext` or the older `get_account_context`), never both. Before you say the context tool is unavailable, search for it by its exact name (`getAccountContext`), then by the connector names ("Pipedrive Beta", then "Pipedrive"), and retry the search once. Only then use the documented fallback.
