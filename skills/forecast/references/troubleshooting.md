# Forecast troubleshooting

- **Stages or open deals unavailable:** no CRM forecast is produced. Explain the failed evidence and offer a self-contained pasted list/CSV; label that result as pasted and do not store it in history.
- **Account context unavailable (tool absent, call fails, or no stages returned):** the older connector lacks `getAccountContext`, or the response is incomplete. No stage tool exists, so there is no other stage source. Say "Pipedrive's account context is not available, so I have no stage data. Update the Pipedrive connector and retry." Do not guess stages, scope or stage order.
- **Scripts cannot run (no Bash tool, no `python3`, or the plain command is refused):** follow SKILL.md "No script available": compute in-model from the written rules, open with the fixed "Not code-checked" line, and offer no dashboard, checkpoint, history entry or saved result.
- **Activities unavailable:** calculate from readable signals, say which haircut evidence and deal count are unavailable, and mark the adjusted forecast potentially optimistic.
- **All stages are 100%:** show unweighted first, then offer editable assumed probabilities or continued unweighted output.
- **Mixed currencies:** keep separate sections. Do not request or invent exchange rates.
- **No matching target:** show `Not configured`, not zero.
- **Dashboard unavailable:** retain the text forecast. Unknown or unvalidated clients do not receive a dashboard option.

## Connector errors, missing tools and tool names

- **403 on a read:** stop that read. Say once: "Pipedrive refused that request. Reconnect the Pipedrive connector in your client settings and try again." Never retry it and never substitute another tool for it. In deal-triage the same applies to a write: report it as not written and keep the item as a proposal only.
- **A tool is missing after a tool search:** say "<tool> is not available in this workspace; ask your Pipedrive admin to re-add the connector." Name the tool, stop that step, and never guess its data or use a different tool in its place. Then name the checks that depend on that tool and say they were skipped: without `getNotes`, the notes-mismatch rule R7 and buying signals; without `getActivities`, the silence and overdue rules.
- **The context tool:** call whichever name is registered (`getAccountContext` or the older `get_account_context`), never both. Before you say the context tool is unavailable, search for it by its exact name (`getAccountContext`), then by the connector names ("Pipedrive Beta", then "Pipedrive"), and retry the search once. Only then use the documented fallback.
