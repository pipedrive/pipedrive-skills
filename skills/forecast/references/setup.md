# Forecast setup

Start with a question such as “What revenue could we close next quarter?” Forecast reads Pipedrive and uses the chances already recorded on deals or stages. It never writes to Pipedrive.

## Before the first result

When account context succeeds, follow `references/account-context-guide.md` before
the fallback questions below. Use verified pipeline names in scope questions and saved
labels, with verified stage ranges to disambiguate duplicate names. Keep IDs internal. Use
authenticated `current_user.id` for “my deals”. Reuse a saved company URL or valid
authenticated `company_domain`; ask only when neither supplies it. Never replace a
pipeline name with its first stage name during scope confirmation.

1. Use the period already requested. Ask which period only when it is missing; resolve “next quarter” using today's date and the saved fiscal calendar.
2. Use the confirmed saved scope or the scope in the request. Ask “Your deals, the whole team, or a particular pipeline?” only when it is missing. Confirm the owner behind “my deals” when necessary, and preserve excluded pipelines. When the request itself says “my deals”, “only my deals” or “mine”, the owner answer is Me with `owner_source: "request"`; nothing else is inferred from the request.
3. Read existing local settings. Follow `references/company-url-guide.md`: reuse a saved company URL, or ask once for the missing URL with Skip for now, then calculate the result. A target, manager commitment, custom probabilities and risk adjustments are optional; do not ask for them before the first result. If config is absent, use an empty JSON object in the run's evidence directory and pass confirmed scope with `--lens`. Do not create persistent assumptions to make setup finish.

Show the computed amount, whose deals it covers and its main limitation. If the CRM has no usable probabilities, show open deal value and explain that it is not a likelihood estimate. An unread deal page still prevents a complete forecast; simplifying setup must not hide missing evidence.

## After the result

Offer optional improvements without making the user complete another checklist:

- **“Compare this with a sales target?”** A missing target remains `Not configured`. The MCP cannot read Pipedrive Goals, so enter a target locally for the selected period, currency and scope only when the user wants a comparison.
- **“Review how inactive deals affect this estimate?”** When adjustments are absent, explain that stale activity, missing follow-ups and passed close dates have not reduced the estimate. If the user asks to review settings, explain the starter choices in ordinary words: keep 50% of the probability for silence, 70% for no next activity, and 50% for a passed close date. These are assumptions, not measured conversion rates. Restate the choices and require explicit confirmation before saving them. Use the already fetched evidence to recompute.
- **Dashboard / Claude Doc / Visual artifact / Both / All three.** Keep the chat result usable first. Both creates Dashboard and Claude Doc; All three adds the separate Visual artifact. Use `references/question-guide.md` for selectable output and optional style choices. An explicit dashboard request uses Combined without another mandatory design question. Honor choices already made.

Never interpret declining an optional improvement as permission to apply defaults. When CRM probabilities are unusable, show the unweighted result first, then offer local editable probabilities or continued unweighted output; never silently choose a ladder.

## Settings when requested

A manager commitment is a separately entered judgment, not the calculated estimate. Local targets and commitments are keyed by period, currency and scope; never add overlapping targets.

Forecast retains current fiscal-year snapshots and one end-of-period snapshot for older periods. Fiscal year begins in January unless confirmed saved settings change it. Keep these details in saved evidence or a requested settings discussion, not a mandatory onboarding question.

Scheduled mode uses saved settings only. It asks no questions, changes no settings, offers no dashboard, and stops with one clear message when required scope or period is missing.

## Tester footer

Testers can set `"tester_footer": true` in `.pipedrive-sales/config.json` (off by default, version-1 and version-2 settings). Each result then ends with one line, `Tester: <skill> <version> · <N> calls · ~<T> tokens of Pipedrive data read by the scripts (estimated)`. Every saved result carries `skill`, `version`, `calls` and `estimated_tokens` whether or not the footer is on.
