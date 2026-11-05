# Pipedrive Sales

Sales workflows for Pipedrive, built for how Pipedrive works: deals move through stages in board order, every deal's next step is its next activity, stages have rot limits, and people carry Hot, Warm or Cold labels. Ask in plain language. Claude reads your Pipedrive and writes back only after you say yes.

Version 0.7.24 · 9 skills · Apache-2.0

## Connect Pipedrive

Every skill reads your Pipedrive account through the official Pipedrive MCP, which the plugin references (`.mcp.json`, `https://mcp.pipedrive.ai/mcp`). Installing the plugin gives Claude no access by itself: connect Pipedrive from the plugin's **Connectors** tab, or sign in the first time Claude calls it. Claude then sees what your Pipedrive role allows. See `CONNECTORS.md`.

## Install

**Claude Cowork and claude.ai**

- From this repository: **Customize → Plugins → Add → Add marketplace**, enter `pipedrive/skills`, then install `pipedrive-sales`.

**Claude Code**

```bash
claude plugin marketplace add pipedrive/skills
claude plugin install pipedrive-sales@pipedrive
```

Start a new session afterwards. The skills show up as `pipedrive-sales:<skill>`.

## Updates

Each release is a tag (`vX.Y.Z`) plus a GitHub release. For a marketplace install, run `claude plugin marketplace update pipedrive` or select **Check for updates** under **Customize → Plugins**.

## What you can ask

| Skill | Ask it | What you get |
|---|---|---|
| `pipedrive` | "show me my open deals", "move the Acme deal to Proposal Made", "what can you do?" | The front door. Quick answers about deals, persons, organizations, activities, leads and products, small record changes after a yes, and a hand-off to the right workflow below |
| `daily-brief` | "good morning", "what's on my plate today?" | Today's meetings with deal context, overdue and due-today activities, deals with no next activity, rotting deals, deals closing soon, and suggested next activities checked against what's already scheduled |
| `pipeline-brief` | "how's my pipeline?", "which deals are rotting?" | Your board in stage order with counts and value per currency. Every open deal is sorted into act today, this week, or nothing found, with numbered next activities you can pick |
| `deal-triage` | "what should I do about the Globex deal?" | One deal: where it sits, what's holding it back, what's already scheduled, and the single next activity that would move it |
| `forecast` | "what's my forecast for this quarter?", "what if the Acme deal slips?" | Won to date, weighted figures where Pipedrive holds probabilities, best case, passed close dates, a by-stage view, and what-if answers. Read-only |
| `call-prep` | "prep me for my call with Fay Moss" | A call brief: the deal's stage and close date, who's on the call, a dated timeline, and a call plan. It can save the brief as a note on the deal |
| `contact-details` | "pull up Cara Diaz", "who do we know at Initech?" | A read-only view of one person or organization: label, owner, deals, recent activities and notes, and flags |
| `sales-outreach` | "follow up with Cara about the proposal" | One email draft grounded in the deal's notes, activities and synced mail, plus next-activity options to keep the deal moving |
| `meeting-follow-up` | "just got off the phone with Eli Novak, they confirmed budget" | Closes the activity that just happened with its outcome, saves a note, schedules the next activity, and proposes deal and label changes for your yes |

## How writes work

- **Nothing is written until you confirm.** Every skill shows exactly what will change and asks first: `Create it? (yes / edit / skip)`, or a numbered list you pick from. A stage change always gets its own yes.
- **Text in your CRM is treated as data.** Instructions written inside notes, emails or record names are never followed.
- **Failed writes are checked, not retried blindly.** After an error, the skill re-reads the record to see whether the change landed before it offers to try again.
- **Someone else's record is flagged.** If a change touches a deal or person owned by a colleague, the confirm prompt says so.

## Good to know

- **Rotting is computed** from each stage's rot limit and the deal's time in stage, so it can differ from the rot badge in the Pipedrive UI. The skills say so.
- **Currencies are never added together.** Totals come per currency.
- **What the Pipedrive MCP can't see:** goals or quotas, deal field history (so a slipped close date can't be detected), and a deal's full participant list. The skills tell you when that matters instead of estimating.
- **Records aren't deleted.** The Pipedrive MCP has no delete tools.
- **Web search:** call-prep and the account-research agent can look up public company news for context. That sends the organization's name to a web search. Nothing else leaves your Pipedrive and Claude session, and nothing is stored on disk.

## License

Apache-2.0. See [LICENSE](LICENSE).
