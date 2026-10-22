# Pipeline Brief dashboard design

The Pipeline Brief dashboard is a deterministic, self-contained presentation of an
already computed result. It is a rep-facing aid, not a second ranking or inference
system.

## Tokens

| Token | Value | Use |
| --- | --- | --- |
| Pipedrive green | `#017737` | primary actions, bars, and active emphasis |
| Deep green | `#10261d` | header and high-contrast foundation |
| Mint | `#e8f5ed` | supportive signal-chip background |
| Paper | `#f7faf8` | page background |
| Risk red | `#bd3d38` | at-risk marker, always paired with text |
| Warning amber | `#a96800` | partial-coverage warning, always paired with text |

Use the system font stack only. Do not load fonts or any other remote resource.

## Component contract and order

The reusable components are: header/context, disclosure banner, metric card, action
card, signal chip, evidence disclosure, supporting panel, stage bar, and footer.
Render them in this order: header and context; partial-coverage disclosure when
present; metrics; ranked action cards; supporting panels; stage distribution; audit
footer. The rep action and next step precede technical evidence. Coverage and rule
limitations remain visible beside the affected dashboard, never in a tooltip.

The renderer may format and escape computed values, but it must not score, rank,
rename a risk state, or invent a recommendation. Rule IDs may appear in evidence for
auditability, but never as the primary action copy.

## Accessibility and responsive layout

Use semantic `header`, `main`, `section`, `article`, headings, `details`/`summary`,
and `footer` landmarks. Every colour state has an accompanying text label. Evidence
summaries require a visible keyboard `:focus-visible` outline; stage bars require a
text alternative through an ARIA label.

At 390px the dashboard is one column: metric cards, supporting panels, and stage
labels each stack without horizontal scrolling. Print CSS keeps the disclosure banner,
footer disclosures, and evidence readable and avoids splitting those units where
possible.

## Restraint

Do not use external assets, gradients, stock imagery, animation, external icon packs,
arbitrary charts, or decorative content that competes with sales action. Do not use raw
rule shorthand as primary copy.
