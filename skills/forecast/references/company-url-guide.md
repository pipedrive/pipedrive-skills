# Company Pipedrive URL — shared by all three skills

Read the resolved shared `.pipedrive-sales/config.json` before asking. Use the project settings path when available; use the home settings path only when the project path cannot be used. Once a path is selected, read and update that same file across Pipeline Brief, Deal Triage and Forecast. The home file is not a second independent settings store.

During interactive setup or before a new report, check `output.domain` using `scripts/pipedrive_links.py --config <resolved-config-path>`. A valid saved subdomain or company URL means **do not ask again**, whichever of the three skills saved it. Reuse a confirmed answer already given in this conversation. A missing or invalid value needs one question, after any necessary scope/owner confirmation:

> What is your company's Pipedrive URL? Paste something like https://acme.pipedrive.com, or just acme, so I can link each deal to your account. You can skip for now.

Use the host's selectable question tool and typed-answer field when available, following `references/question-guide.md`. Otherwise ask in chat. Never infer the domain from a customer name, owner name, email or generic pipedrive.com URL. Accept a company subdomain, company hostname or full company URL; save only the normalized subdomain as `output.domain`. Reject another host, credentials, ports or an invalid subdomain and ask for a corrected value. Skip for now keeps this run usable with plain deal labels; do not keep asking during the same run.

For complete first-time setup, include the confirmed domain in the full settings object and use that skill's verified settings writer. When only the URL is missing or being changed, use the standalone command shipped with **each** skill:

```bash
python3 <skill-folder>/scripts/pipedrive_links.py --config <resolved-config-path> --company-url 'https://acme.pipedrive.com'
python3 <skill-folder>/scripts/pipedrive_links.py --config <resolved-config-path>
```

Quote the supplied value as a literal shell argument. The writer changes only `output.domain`, preserves all other settings, writes atomically and reads the file back before reporting persistence. Verify the returned domain and path before saying saved. Show the normalized company URL. Another skill's setup must preserve this saved domain unless the user explicitly changes or clears it. Failure to save means session-only: do not claim persistence. On a host without code execution, retain the answer in the conversation and explain the persistence limit.

Scheduled runs never ask for this optional presentation setting or change it. Pending triage confirmations and output-only requests for a saved result also do not interrupt the user's selected action with setup questions. Use the domain already present in the saved result. If a sandbox reset removed the settings file, explain that the saved file is no longer available; restore the entire confirmed settings object, including the domain, when available. Do not pretend a deleted file remains saved.

All three computations carry the shared domain into `output_domain` in the saved result. Only link positive deal IDs actually present in that result, using `https://<domain>.pipedrive.com/deal/<id>`. Lead IDs are not deal IDs. These are navigation links; dashboards still cannot load remote resources or send CRM data.
