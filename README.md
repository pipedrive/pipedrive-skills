# Pipedrive Sales

Pipedrive pipeline brief, deal triage and forecast skills on the official Pipedrive MCP.



Version 0.7.22. A release workflow in the source repository builds this repository; the next release overwrites every file.

## What's inside

| Skill | What it does |
|---|---|
| `pipeline-brief` | Pipeline status: what moved, what closes this week, what stalled, ranked by your team's rules. Reads only. |
| `deal-triage` | Flags stalled or at-risk deals and proposes one next activity per deal. Writes only after you confirm each one. |
| `forecast` | Revenue forecast for a period, with coverage against target and risks. Reads only. |

Every skill reads your Pipedrive account through the official Pipedrive MCP connector, which the plugin references (`.mcp.json`, `https://mcp.pipedrive.ai/mcp`). Installing the plugin gives Claude no access by itself: you connect Pipedrive from the plugin's **Connectors** tab, or sign in the first time Claude Code calls it, and Claude then sees what your Pipedrive role allows.

## Where it runs

- **Claude Cowork and Claude Code**: supported. The plugin's hooks save each Pipedrive response for the bundled scripts, check each reply the scripts produce, and let a write reach Pipedrive only after you confirm it, and only once: a write that succeeded is not sent again.
- **claude.ai chat**: the skills load, but chat does not run plugin hooks, so nothing is saved and no write is guarded. The skills say so and stop. Use Cowork or Claude Code.

## Install

**Claude Cowork and claude.ai**

- From the directory, once the plugin is listed: **Customize → Plugins → Discover**, select the plugin, **Add**. On Team and Enterprise plans an Owner can set it to **Installed by default** or **Required** for the organization.
- From this repository: **Customize → Plugins → Add → Add marketplace**, enter `pipedrive/skills`, then install `pipedrive-sales`.

Then connect Pipedrive on the plugin's **Connectors** tab and start a task, for example "give me my pipeline brief for this week".

**Claude Code**

```bash
claude plugin marketplace add pipedrive/skills
claude plugin install pipedrive-sales@pipedrive
```

A plugin added on claude.ai also reaches Claude Code as a synced plugin at the next session start.

## Data

- **What it reads**: deals, activities, notes, people, organizations, stages and your account context, through the Pipedrive connector, with your own Pipedrive permissions.
- **What it sends**: nothing to any service other than Pipedrive. When you ask for a dashboard or a Claude Doc, the host's own Artifact or Claude Docs tool publishes it in your Claude account.
- **What it stores**: a `.pipedrive-sales/` folder in the working folder you run it in. It holds your saved settings (`config.json`), the run records the scripts need, and copies of Pipedrive responses the hooks saved for them (`runs/captured/`), which the scripts delete after 7 days, and the id of the Pipedrive user you are signed in as (`runs/current-user.json`), which the write checks use. These are plain files with CRM data in them: if the working folder is a git repository, add `.pipedrive-sales/` to its `.gitignore`. Delete the folder to remove everything the plugin kept.
- **What it writes to Pipedrive**: only `deal-triage` writes, and only through four tools (`addActivity`, `updateActivity`, `addNote`, `updateDeal`), each after you confirm that item. A write reaches Pipedrive only with the exact parameters a preflight script produced; the plugin's write guard denies anything else. The other skills never write.

## Experimental: Codex and ChatGPT

Unsupported. This release ships no Codex or ChatGPT manifest. A Codex-format marketplace exists in the preview build, for testing only. Whether ChatGPT runs plugin hooks is not confirmed, so the write guard and the saved responses may not exist there.

## Updates

Each release is a tag (`vX.Y.Z`) plus a GitHub release with per-skill zips and a SHA-256 manifest. A directory install updates on its own. For a marketplace install, run `claude plugin marketplace update pipedrive` or select **Check for updates** under **Customize → Plugins**.

## Support and license

Report problems on this repository's issue tracker. Never include customer names, account ids, OAuth tokens or API keys in an issue. Licensed under Apache-2.0; see `LICENSE`.
