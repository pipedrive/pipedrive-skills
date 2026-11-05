# Connectors

## Required: the official Pipedrive MCP

The plugin references Pipedrive's hosted MCP server in `.mcp.json` (`https://mcp.pipedrive.ai/mcp`). Nothing is bundled: installing the plugin gives Claude no access by itself. Connect Pipedrive from the plugin's **Connectors** tab, or sign in the first time Claude calls it. Claude then sees what your Pipedrive role allows.

Skills reference Pipedrive tools by bare name (`getDeals`, `addActivity`, `getAccountContext` and so on). Your client shows them with a prefix. Same tool.

If Pipedrive isn't connected or its first call fails, the skill says so and stops. It does not guess data.

The skills run on **vanilla Pipedrive**: they never read or write custom fields and never change account configuration (pipelines, stages, labels, fields).

## Optional connectors

The skills check for these at runtime and use them when present. When one is missing, the skill skips that source silently and works from Pipedrive alone.

- **Email (for example Gmail):** extra email history for call prep, follow-up and the daily brief. Pipedrive's own synced mail always comes first.
- **Calendar (for example Google Calendar):** today's meetings in the daily brief and attendee lookup in call prep.
- **Web search:** company context in call prep and the account-research agent.
