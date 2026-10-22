# Contact-link edit

A contact-link edit adds the deal's linked contact to an existing Activity. It is a separate item with its own acceptance; SKILL.md section 5 names the rule, this file the steps.

First show a separate proposal naming the
deal, Activity id and subject, and the deal's linked contact by id and its verified
name if available. Never guess a contact name. Wait for
an explicit acceptance of this contact-link edit; acceptance of a reschedule or another
triage item does not include it. After acceptance, freshly read `getDeal(id)` and
`getActivities(ids=["<activity-id>"])`, save both complete raw responses, then run
`scripts/contact_link.py --deal-response <saved-deal-response> --activity-response
<saved-activity-response> --deal-id <deal-id> --activity-id <activity-id> --person-id
<accepted-contact-id>`. Use only its exact `params` with `updateActivity`. The script
requires an open deal, the same linked contact, an undone Activity on that deal,
and an explicitly empty contact and participants list. It returns only
`id` and `participants:[{"person_id":<deal-person-id>,"primary":true}]`; never
include another person or another Activity field. If the preflight fails, stop and
ask the user to review the current records. Re-read the Activity after the write
and confirm its deal link and primary person match before reporting success.
