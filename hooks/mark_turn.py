"""UserPromptSubmit hook: record the rep's current message in .pipedrive-sales/runs/turn.json.

workflow.py page reads it so one "show more" serves one page: a second page call in the same turn reprints the page
already served (0.7.2 live runs: the model ran page several times per turn and the unseen pages were lost). It writes
only where .pipedrive-sales already exists, prints nothing and always exits 0, so it never touches the conversation. Since 0.7.13 (decision A,
ADR-043 note) the marker also carries write_intent: whether the message reads as an accept, an edit, a skip or a change
request, so the Stop hook can tell an unscripted reply to such a turn from a read turn. Only the flag is stored, never
the message. Since 0.7.14 (decision 1A, ADR-041 note) a message that names a skill creates the folder where none exists,
so the first skill run of a fresh workspace has a marker and its reply is checked (0.7.13 run (iv): the chat skipped the
config message, the folder did not exist at "Forecast this month", and the forecast reply went unchecked). Any other
message where the folder is missing still does nothing.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid

# An accept, edit, skip or change request: the turns whose reply must be script output (write_confirmation.py --ask /
# --turn, compute.py confirm). A read request ('what moved this week') can match too; the Stop hook acts only where a
# writing skill's saved result exists, and a false positive costs one send-back that ends in the question script's text.
# The mark branch needs an object between the verb and the status word (0.7.16 phase 1, ADR-043 note): a pasted report's
# "Marked done is when its completion was recorded" had matched and sent a read reply back once. A request that names deals
# and asks for a call, a meeting or a demo is a change request too (0.7.19, decision 4A): 0.7.18 check (i) answered
# "deal … and deal … need a call this week" with an item line of the chat's own and no script run.
WRITE_INTENT = re.compile(
    r"\b(accept(ed|s)?|yes to \d|yes,? (accept|do it|write|move|go ahead)|edit (item )?\d|skip (item )?\d|skip all|move (the|it|that|this)?\b.{0,80}\b(to|by)\b"
    r"|reschedule|change (the |its |that )?(date|due|close)|mark(ed)? (?!(done|won|lost)\b)\S.{0,60}?\b(done|won|lost)|set (the |its )?(due|close|status)"
    r"|(need|needs|book|schedule|set up) (a |an |another )?(call|meeting|demo))\b", re.I | re.S)

# A message that names a skill or asks for what one does: the folder is created for it where none exists (0.7.14,
# decision 1A), so the first run of a fresh workspace has a marker. Skill names, their slash forms and the request words
# the skill descriptions trigger on; bare "deal" is not in the list, so an unrelated workspace gets no folder. Since
# 0.7.20 the bare product name is not in it either: a question to the connector that says "Pipedrive" and names no skill
# created the folder, and the read capture then saved the answer, which is what the capture gate exists to prevent
# (release run pre-check, 21 Oct). The slash form still matches on the skill word after the colon.
SKILL_NAMED = re.compile(
    r"\b(pipeline|brief|triage|forecast|follow[- ]?up|outreach|kitchen|what moved|closing this (week|month)"
    r"|on track|stuck deals|stalled deals|top customers|meeting notes)\b", re.I | re.S)


def main():
    try:
        event = json.loads(sys.stdin.read() or '{}')
    except ValueError:
        event = {}
    folder = Path(os.environ.get('CLAUDE_PROJECT_DIR') or event.get('cwd') or os.getcwd()) / '.pipedrive-sales'
    prompt = event.get('prompt') if isinstance(event.get('prompt'), str) else ''
    created = False
    if not folder.is_dir():
        if not SKILL_NAMED.search(prompt):
            return 0
        try:
            folder.mkdir()
        except OSError:
            return 0
        created = True
    marker = {'turn': str(event.get('prompt_id') or uuid.uuid4()), 'session_id': event.get('session_id'),
              'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'write_intent': bool(WRITE_INTENT.search(prompt))}
    if created:
        marker['folder_created'] = True
    try:
        runs = folder / 'runs'
        runs.mkdir(exist_ok=True)
        if created:
            # turn.json is rewritten every message; this one stays, so a live-run zip shows which message created the folder.
            (runs / 'folder-created.json').write_text(json.dumps({'turn': marker['turn'], 'at': marker['at']}), encoding='utf-8')
        temporary = runs / 'turn.json.tmp'
        temporary.write_text(json.dumps(marker), encoding='utf-8')
        os.replace(temporary, runs / 'turn.json')
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
