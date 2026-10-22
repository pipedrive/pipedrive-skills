# Selectable questions

Use this when an interactive skill needs an unanswered choice: setup or scope, scan continuation, report output, dashboard style, optional Forecast settings, or a Deal Triage item decision. Honor answers already given in the request or this conversation. Do not add a question just because a question tool exists. Scheduled runs that cannot receive user input keep their existing no-question behavior.

## Use the current host's question tool

Check the tools exposed in this session for a host question tool that presents selectable options and returns the user's answer. Use `AskUserQuestion` when exposed, or the host's equivalent with its documented schema. Do not guess a tool name, manufacture clickable Markdown, or infer support from the Cowork name or a saved dashboard capability profile. Question support is independent of dashboard and Claude Doc support.

Ask one decision at a time, with short option labels and one sentence explaining each option. Put a recommendation first when there is a supported reason. Use single selection for exclusive choices. Allow the host's typed alternative when supported; do not add a duplicate Other option if the host already supplies one. If the host cannot return typed answers, say the user can reply in chat with their own choice. Read the returned selection and typed text, including accompanying notes, before continuing. A typed answer takes precedence over a default; conflicting answers need clarification.

If the tool is absent, denied, fails, or cannot collect an answer here, ask the same decision once in chat as a short question with numbered choices and “Or type your own choice.” Do not keep retrying the tool. A skipped, cancelled, empty, timed-out, or automatically selected default without an explicit user answer is not user approval: do not start optional output, more scanning, save settings, or accept a CRM item from it. Keep the report available and leave the decision pending or stop if the user asked to stop.

Deliver the completed report and its limitations before optional questions. Keep computed Markdown verbatim except for its exact trailing conversational question: that question may be moved into the host question tool, or replaced with the concise numbered fallback. For version-two replies, use `workflow.py questions` for a separate host card, or `reply` and `check-reply` with `--question-fallback` to include one numbered question inside the checked response. Never edit or append to a checked reply. Keep the saved Markdown unchanged and preserve every count, deal line, warning, evidence limitation, and unread boundary. Do not repeat a long text question alongside its selectable version. If the host hides text next to a question card, include the essential limitation in the question itself and ensure the report remains accessible in chat.

When a requested task needs additional setup, explain only its missing prerequisites
and offer Complete setup or Skip for now. Follow `workflow.py setup-check`; on Complete
ask one missing question at a time, reusing accepted context and choices. On Skip keep
the available result and state what cannot be done. Do not mistake unread data or an
unavailable host tool for missing setup, or save unanswered choices as defaults.

## Report output

Ask “What would you like me to create?” with Dashboard, Claude Doc, Visual artifact, Both, All three, and Nothing else where supported. Visual artifact is a tailored interactive view of the saved report; read `visual-artifact-guide.md` when selected. Both means Dashboard plus Claude Doc; All three adds the separate Visual artifact. Only offer routes supported by the observed host capabilities; if support is unknown, explain that availability still needs checking rather than promising delivery. HTML support permits Dashboard and Visual artifact; native Doc support permits Claude Doc. A separately available local export must be labelled Markdown/PDF, never Claude Doc. A host card takes at most four choices, so `workflow.py questions` already splits this one: with both routes the card is Dashboard, Claude Doc, Both and More options, and More options opens a second card with Visual artifact, All three and Nothing else; ask the cards as printed, never a six-option card. Where no question tool exists, use the numbered fallback without dropping Visual artifact. A user may type another request.

Output selection authorizes only the chosen report presentation. It never accepts a triage proposal or a CRM write. Nothing else keeps the written report usable; a dashboard decline remains session-only unless the user separately requests a lasting preference.

## Dashboard style and destination

Only when a dashboard is selected and the user has not already specified a style, ask “How should I build the dashboard?”:

- Combined (recommended): Arrange tested charts; consistent labels and interactions, with less chart-design freedom.
- Claude-designed: Tailor the layout and charts; needs extra checks for accurate facts, labels, and interactions.

This optional style choice does not block an explicit dashboard request: when the user simply requests a dashboard, announce Combined and proceed under the existing delivery rules. When the user is answering an output offer, ask the style question once. Honor an existing style preference. Never choose Claude-designed from a bare approval.

When several delivery destinations are verified, ask which destination using the same question tool; retain their storage/sharing notices in the option descriptions. Output and style choices do not bypass delivery checks.

## Partial scan

After the partial brief, ask “What should I check next?” with Continue scanning, Choose a pipeline, Use a saved filter, and Stop here. Explain that continuation uses bounded batches of unread pages. Include the checked-record count and an exact remaining count only if saved responses provide it; otherwise say the remaining count is unknown. Retain the warning that totals and recommendations cover only checked deals.

Continue only after an explicit selection or typed request. Preserve the current scope, sort, fields, and unread cursor when continuing. A narrower scope starts a new scan; ask for the pipeline or filter only if the answer did not supply it. Stop here performs no further fetch. An unreadable raw-response boundary is not a resumable cursor: offer only alternatives supported by the saved evidence, never a way around lossless-response requirements.

## Deal Triage and settings

For a pending item that needs confirmation, the question's text is what `write_confirmation.py --ask N` prints for that item (item number, deal, action, subject, type and due date from the saved result); never word it yourself. Offer Accept item N, Edit item N (a new due date only), and Skip item N. Review only may be offered when useful. Ask for one item at a time in per-item mode; in batch mode collect named item selections or a typed batch command, then follow the existing confirmation rules. Do not add a new output/style question while an item confirmation is unresolved. When the report has no items and the user asks for a change, the reply is `--ask 1`'s refusal text, with no write offered.

Only a user's explicit answer tied to that exact proposal counts as item acceptance. Editing means revise and reconfirm, not write. A generic Yes, output selection, dashboard click, default, or question-tool success without an answer does not accept an item. Status/stage changes and contact-link repairs still need their separate named confirmation. Fresh reads, preflight checks, write permissions, and saved write-response verification remain required. Never retry an already written item.

Use the same approach for optional Forecast settings: offer review, edit, or keep the current setting after delivering the result. A choice to review is not acceptance of a setting change. Restate the proposed values and obtain explicit confirmation before saving. For setup, scope, or owner identity, ask only the required unresolved question and preserve the skill's existing confirmation order.

All three is offered only when both HTML/artifact delivery and native Claude Docs are supported. It means three distinct outputs from the same saved result. If six choices exceed the host tool limit, use the numbered fallback or split the question; never silently drop an output. A prior choice of Both stays Dashboard plus Claude Doc.

## Fields the account context does not show

When the rep names a custom field you cannot see in the account context, ask which field they mean. Never treat it as empty, absent or unused. The context returns at most 500 custom fields per record type; when `field_counts` flags that record type `may_be_incomplete`, say the field list may be incomplete.

## Setup questions

Never assume a setup answer. Ask each missing one with the selectable question tool, one at a time; a question with no options is a typed answer. A skipped, cancelled or automatic answer is not an answer: leave setup pending. The answer mapping for setup is in `workflow-guide.md`, "Setup answers from the selectable questions".

A setup card that ends with `More pipelines` or `More people` is one page of a longer list: when the rep picks it, write the next `scope_page` or `owner_page` into the answers and run `setup` again; a rep may name a choice from any page.
