"""Stop hook: make the chat's last message the text a script saved for it (.pipedrive-sales/runs/expected-reply.json).

The scripts print a checked reply (brief, triage or forecast reply, show more page, meeting proposal, accept turn
confirmation) and save it with pdcore.save_expected_reply. In the 0.7.7 Cowork runs the chat retyped that text: one
deal showed another deal's figures, show more answered "(above)", a confirmation lost its tester footer. The reply must
start with the saved text, unchanged; text after it is allowed, because a skill can need to ask something the script
does not print (DT-02, 16 Oct: the triage "mark as WON — confirm?" question after an accept turn's confirmation).
Curly and straight quotes and dashes count as the same. A hook cannot rewrite a message, so on a difference it sends
the chat back once with the saved text (decision 1, 16 Oct 2026). If the second message still differs it shows a
one-line warning. Every check it makes appends one line to runs/reply-check.log (kind, turn, result), so a live run shows
whether a reply matched first time or on a retry (0.7.9). A selectable-question answer does not start a new turn, so
when the rep picks a follow-up (a dashboard) the turn's last message is not the checked reply. The check therefore
passes when any assistant message written since the text was saved opens with it (transcript_path; 0.7.9 Cowork run
(c) resent a whole forecast after the dashboard summary). Since 0.7.17 (ADR-043 note) that lookup also reads the input
of a SendUserMessage tool call, which is how Cowork shows a mid-turn message (0.7.16 check (ii-b): the report sat
there and the chat was sent back), and accepts the checked text after a preface of the chat's own, which counts
as its own text. When the lookup finds nothing, the log line says why, in
names and counts only (0.7.10: in Cowork it found nothing and nobody could tell why). The chat's own text (what follows
the checked reply, and the whole last message when the reply was sent earlier in the turn) may not name a record by
number: the DT-02 rerun (17 Oct) added "deal 45 … activity #88" after a matched confirmation, which reps never see from
a script (HC-04, pdcore.visible_record_ids). Such text is sent back once like a retyped reply (0.7.11, decision A). It
acts only on text saved in the current turn (runs/turn.json, written by mark_turn.py), never touches anything outside
.pipedrive-sales and exits 0. Since 0.7.13 (decision A, 18 Oct, ADR-043 note) a turn whose message reads as an accept,
edit, skip or change request (turn.json write_intent, from mark_turn.py) and for which no script saved a reply is a
miss as well, where a writing skill's saved result exists: three Cowork runs answered such a turn in the chat's own
words with nothing to check. The chat is sent back once to run the question or turn script; a retry with still nothing
saved gets the one-line warning. Since 0.7.19 (decision 1A revised, ADR-043 note) the hook also counts the record numbers in the
chat's own mid-turn messages since the save (the SendUserMessage inputs, minus the checked text where one carries it) and
logs them as mid_turn_ids, without a send-back (text parts too: 0.7.19 check (i) sent them in one): 0.7.18 check (i) sent two deal numbers as a message of its own before the
report, and 0.7.19 phase 0 showed that Cowork renders such a message before any hook can answer, so a check can only
measure it after the rep has read it; the frame now says a record is named by its name in any message. Since 0.7.19 phase 2 (ADR-024 note) a turn whose checked
reply leaves questions for the rep (runs/card-due.json, written by workflow.py check-reply) and ends without the questions
command having run (no asked_at) is sent back once, naming that command: 0.7.18 release run (a) and 0.7.19 phase 0 run 1
ended the turn on the forecast report and the rep typed the request for the outputs.
"""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

WARNING = 'Pipedrive Sales: this reply differs from the checked text the script printed; ask for it again.'
CARD_WARNING = 'Pipedrive Sales: the output question was not asked after the report; ask for the dashboard or doc in words.'
UNSCRIPTED_WARNING = 'Pipedrive Sales: this accept or change request was answered without the script; ask for it again.'
WRITE_SKILLS = {'deal-triage': ('run python3 <skill folder>/scripts/write_confirmation.py --ask <n> for an item not yet accepted or a '
                                'request the report did not propose (1 when the report has no items), or --turn --accepted <n,…> after '
                                'the writes'),
                'meeting-follow-up': 'run python3 <skill folder>/scripts/compute.py confirm --result … --accepted <n,…>'}

# A record name the saved text links; the chat may repeat it even when it looks like a number ("Deal 2", 0.7.4 decision 2A).
LINKED_NAME = re.compile(r'\[([^\]]+)\]\(https?://[^)\s]+\)')


def load_visible_record_ids():
    """pdcore.visible_record_ids from a skill copy shipped beside this hook (every skill folder carries one, byte-identical
    to shared/pdcore.py). None when no copy loads: the reply check still runs, the ID check is skipped and logged."""
    for copy in sorted((Path(__file__).resolve().parent.parent / 'skills').glob('*/scripts/pdcore.py')):
        try:
            spec = importlib.util.spec_from_file_location('pdcore_for_hook', copy)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.visible_record_ids
        except Exception:  # noqa: BLE001 - a broken copy must not stop the reply check
            continue
    return None


SAME = str.maketrans({'\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"', '\u2013': '-', '\u2014': '-',
                      '\u00a0': ' '})


def normalized(text):
    return '\n'.join(line.rstrip() for line in str(text).translate(SAME).strip().splitlines())


def opens_with(message, expected):
    """True when the message starts with the expected text, line for line; anything after it is the chat's own."""
    sent, wanted = normalized(message), normalized(expected)
    return sent == wanted or sent.startswith(wanted + '\n')


def own_text(message, expected):
    """The chat's own text in a message that opens with the expected text: what follows it."""
    return normalized(message)[len(normalized(expected)):].strip()


def record_ids(text, expected, finder):
    """The record numbers the chat's own text shows, deduplicated in order; the saved text's linked names are not numbers."""
    if not text or finder is None:
        return []
    return list(dict.fromkeys(finder(text, LINKED_NAME.findall(str(expected)))))[:8]


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def parsed_time(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def leading_match(sent, wanted):
    count = 0
    for a, b in zip(sent, wanted):
        if a != b:
            break
        count += 1
    return count


SENT_TOOLS = ('SendUserMessage',)


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


def sent_messages(record):
    """(kind, text) for each message a transcript record showed the rep: the record's text parts as one 'text', and the
    input of each SendUserMessage tool call as a 'message'. Cowork sends a mid-turn message through that tool, not as a
    text part (0.7.17 phase 0: the 0.7.16 check (ii-b) report sat there, and the lookup read text parts only)."""
    content = (record.get('message') or {}).get('content')
    if isinstance(content, str):
        return [('text', content)] if content else []
    texts, messages = [], []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get('type') == 'text' and part.get('text'):
            texts.append(part['text'])
        elif part.get('type') == 'tool_use' and str(part.get('name')) in SENT_TOOLS:
            text = '\n'.join(s for s in strings_in(part.get('input')) if s)
            if text:
                messages.append(('message', text))
    return ([('text', ''.join(texts))] if texts else []) + messages


def contains(message, expected):
    """(lead, after) when the message carries the expected text whole on its own lines, with the chat's words before
    it: a reply shown earlier in the turn may be prefaced ("Here is the forecast:"); the preface is the chat's own text,
    like trailing text. None when absent or only partly there. An opening match is ('', after)."""
    sent, wanted = normalized(message), normalized(expected)
    if not wanted:
        return None
    index = sent.find(wanted)
    while index >= 0:
        end = index + len(wanted)
        if (index == 0 or sent[index - 1] == '\n') and (end == len(sent) or sent[end] == '\n'):
            return sent[:index].strip(), sent[end:].strip()
        index = sent.find(wanted, index + 1)
    return None


def sent_earlier(event, expected, last=''):
    """(found, lookup, own): found when a message shown since the text was saved carries it (an assistant text part or a
    SendUserMessage call, opening with it or after a preface); own is what that message added before and after it. The
    last message itself is skipped: it is held to opening with the text (main). lookup
    says why not, in names and counts only, never message text: the transcript holds CRM data. In the 0.7.9 Cowork rerun
    2 the lookup found nothing and left no trace of why (0.7.10)."""
    saved_at = parsed_time(expected.get('at'))
    path = event.get('transcript_path')
    lookup = {'path': path, 'saved_at': expected.get('at')}
    if saved_at is None:
        return False, dict(lookup, why='no_saved_at'), ''
    if not path:
        return False, dict(lookup, why='no_path'), ''
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return False, dict(lookup, why='missing'), ''
    except (OSError, UnicodeDecodeError) as error:
        return False, dict(lookup, why='unreadable', error=type(error).__name__), ''
    wanted = normalized(expected.get('text') or '')
    last_sent = normalized(last)
    parsed, assistant, since, with_text, with_message, closest = 0, 0, 0, 0, 0, 0
    types, keys, newest_at, untimed = [], None, None, False
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        parsed += 1
        keys = keys or sorted(record)[:12]
        if str(record.get('type')) not in types and len(types) < 8:
            types.append(str(record.get('type')))
        if record.get('type') != 'assistant' or record.get('isSidechain'):
            continue
        assistant += 1
        at = parsed_time(record.get('timestamp'))
        if at is None:
            untimed = True
            break
        newest_at = newest_at or record.get('timestamp')
        if at.timestamp() < saved_at.timestamp() - 1:
            break
        since += 1
        messages = sent_messages(record)
        with_text += any(kind == 'text' for kind, _ in messages)
        with_message += sum(kind == 'message' for kind, _ in messages)
        for kind, text in messages:
            if last_sent and normalized(text) == last_sent:
                continue
            carried = contains(text, expected.get('text') or '')
            if carried is not None:
                lead, after = carried
                found = dict(lookup, why='found', found_in=kind, lead_chars=len(lead), since_save=since,
                             with_text=with_text, with_message=with_message)
                return True, found, (lead + '\n' + after).strip()
            closest = max(closest, leading_match(normalized(text), wanted))
    why = ('empty' if not lines else 'not_json' if not parsed else 'no_assistant' if not assistant
           else 'no_timestamps' if untimed and not since else 'none_since_save' if not since else 'no_match')
    return False, dict(lookup, why=why, lines=len(lines), parsed=parsed, types=types, keys=keys, assistant=assistant,
                       newest_at=newest_at, since_save=since, with_text=with_text, with_message=with_message,
                       closest=closest, expected_chars=len(wanted)), ''


def mid_turn_record_ids(event, expected, finder, last='', since=None):
    """(messages, ids): the chat's mid-turn messages this turn (SendUserMessage calls and text parts alike: Cowork shows
    both, 0.7.19 check (i) sent the numbers in a text part) other than the last message, and the record numbers in their
    own text (the checked text is taken out of a message that carries it). The turn starts at `since` (the marker's time;
    check (i-b) sent the numbers during setup, long before the report was saved), else at the save. Counts only; the hook
    never sends back for these, because the rep has already read them (0.7.19 phase 0)."""
    saved_at = parsed_time(since) or parsed_time(expected.get('at'))
    path = event.get('transcript_path')
    if saved_at is None or not path or finder is None:
        return 0, []
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return 0, []
    text = expected.get('text') or ''
    last_sent = normalized(last)
    messages, ids = 0, []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get('type') != 'assistant' or record.get('isSidechain'):
            continue
        at = parsed_time(record.get('timestamp'))
        if at is None or at.timestamp() < saved_at.timestamp() - 1:
            break
        for kind, sent in sent_messages(record):
            if last_sent and normalized(sent) == last_sent:
                continue
            messages += 1
            carried = contains(sent, text)
            own = (carried[0] + '\n' + carried[1]).strip() if carried is not None else normalized(sent)
            ids.extend(record_ids(own, text, finder))
    return messages, list(dict.fromkeys(ids))[:8]


def log(runs, turn, kind, result, expected, last, lookup=None, trailing=None):
    entry = {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'turn': turn, 'kind': kind,
             'result': result, 'expected_chars': len(expected), 'sent_chars': len(last)}
    if result in ('blocked', 'differs_after_retry'):
        entry['sent_start'] = last[:200]
    if lookup:
        entry['lookup'] = lookup
    if trailing:
        entry.update(trailing)
    try:
        with (runs / 'reply-check.log').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass


def save(path, expected):
    try:
        path.write_text(json.dumps(expected, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass


def card_due(runs, turn, expected):
    """The card record for this turn when its questions command has not run: the chat ended (or is ending) the turn on
    the checked reply without the card (0.7.19 phase 2)."""
    if expected.get('kind') != 'reply':
        return None
    card = read_json(runs / 'card-due.json')
    if not card or card.get('turn') != turn or not card.get('command') or card.get('asked_at'):
        return None
    return card


def send_back_for_card(event, runs, turn, expected, card, text, last):
    """Block the turn once for the unasked card; on a retry with the card still unasked, warn."""
    if not event.get('stop_hook_active'):
        reason = ('The report is checked and sent: do not send it again. The offer of outputs is a question card, not a '
                  'sentence. Run this command from the same folder and ask its first question through the selectable '
                  'question tool, then go on from the answer:\n' + card['command'])
        log(runs, turn, 'card', 'blocked', text, last, None, {'questions': card.get('questions')})
        print(json.dumps({'decision': 'block', 'reason': reason}))
        return 0
    log(runs, turn, 'card', 'card_unasked', text, last, None, {'questions': card.get('questions')})
    print(json.dumps({'systemMessage': CARD_WARNING}))
    return 0


def unscripted_turn(event, runs, turn, marker):
    """An accept, edit, skip or change request answered with no script output (0.7.13, decision A): send the chat back
    once to the question or turn script, then warn. Only where a writing skill's saved result exists."""
    if not marker.get('write_intent'):
        return 0
    skills = [name for name in WRITE_SKILLS if (runs.parent / 'results' / f'{name}-full.json').exists()]
    if not skills or not isinstance(event.get('last_assistant_message'), str):
        return 0
    last = event['last_assistant_message']
    if not event.get('stop_hook_active'):
        reason = ("This turn's message reads as an accept, an edit, a skip or a change request, but no script printed a reply "
                  "for it, so there is nothing to check. Reply only with script output: for "
                  + '; for '.join(f'{name} {WRITE_SKILLS[name]}' for name in skills)
                  + '. Send the text between its marker lines as the whole reply.')
        log(runs, turn, 'unscripted', 'blocked', '', last)
        print(json.dumps({'decision': 'block', 'reason': reason}))
        return 0
    log(runs, turn, 'unscripted', 'unscripted_after_retry', '', last)
    print(json.dumps({'systemMessage': UNSCRIPTED_WARNING}))
    return 0


def main():
    try:
        event = json.loads(sys.stdin.read() or '{}')
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    runs = Path(os.environ.get('CLAUDE_PROJECT_DIR') or event.get('cwd') or os.getcwd()) / '.pipedrive-sales' / 'runs'
    expected_path = runs / 'expected-reply.json'
    expected = read_json(expected_path)
    marker = read_json(runs / 'turn.json') or {}
    turn = marker.get('turn')
    if not turn:
        return 0
    if not expected or expected.get('turn') != turn:
        return unscripted_turn(event, runs, turn, marker)
    last = event.get('last_assistant_message')
    if not isinstance(last, str):
        return 0
    if expected.get('checked'):
        card = card_due(runs, turn, expected)
        if card and event.get('stop_hook_active'):
            return send_back_for_card(event, runs, turn, expected, card, expected.get('text') or '', last)
        return 0
    text = expected.get('text') or ''
    found, lookup = opens_with(last, text), None
    own = own_text(last, text) if found else ''
    if not found:
        found, lookup, earlier_own = sent_earlier(event, expected, last)
        # The checked reply came earlier in the turn: what followed it and the whole last message are the chat's own.
        own = (earlier_own + '\n' + normalized(last)).strip() if found else ''
    finder = load_visible_record_ids() if found and own else None
    ids = record_ids(own, text, finder)
    trailing = {'trailing_chars': len(own)} if found else None
    if found and own and finder is None:
        trailing['ids_unchecked'] = True
    if ids:
        trailing['trailing_ids'] = ids
    kind = expected.get('kind')
    if event.get('stop_hook_active'):
        trailing = {**(trailing or {}), 'reemitted': 'blocked_at' not in expected}
    mid_messages, mid_ids = mid_turn_record_ids(event, expected, finder or load_visible_record_ids(), last, marker.get('at'))
    if mid_messages:
        trailing = {**(trailing or {}), 'mid_turn_messages': mid_messages, 'mid_turn_ids': len(mid_ids)}
    if found and not ids:
        expected['checked'] = ('matched_on_retry' if event.get('stop_hook_active')
                               else 'matched' if lookup is None else 'matched_earlier')
        save(expected_path, expected)
        log(runs, turn, kind, expected['checked'], text, last, lookup, trailing)
        card = card_due(runs, turn, expected)
        if card:
            return send_back_for_card(event, runs, turn, expected, card, text, last)
        return 0
    if not event.get('stop_hook_active'):
        # The send-back names the command that saved the text and the chat runs it again (0.7.18, decision 1A): the
        # 0.7.17 release run (b) retry retyped the report from the pasted marker text and changed one line, so the rep
        # saw the report twice. The paste stays only for a record an older saver wrote without `command`.
        command = expected.get('command')
        if ids:
            reason = ('Your reply opens with the checked text, but what you added after it names a record by number ('
                      + ', '.join(ids) + '). Reps never see record numbers. ')
        else:
            reason = 'Your last message does not start with the checked text the script printed. '
        if command:
            reason += ('Run this command again from the same folder and send its output as the whole message, with '
                       'nothing before it and nothing after it. Do not retype the text from memory or from this message:\n'
                       + command)
        else:
            reason += ('Send the checked text between the two marker lines below again, without the marker lines, with '
                       'nothing before it and nothing after it.'
                       '\n-----BEGIN CHECKED REPLY-----\n' + text.strip() + '\n-----END CHECKED REPLY-----')
        expected['blocked_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        save(expected_path, expected)
        log(runs, turn, kind, 'blocked', text, last, lookup, trailing)
        print(json.dumps({'decision': 'block', 'reason': reason}))
        return 0
    expected['checked'] = 'differs_after_retry'
    save(expected_path, expected)
    log(runs, turn, kind, 'differs_after_retry', text, last, lookup, trailing)
    print(json.dumps({'systemMessage': WARNING}))
    return 0

if __name__ == '__main__':
    sys.exit(main())
