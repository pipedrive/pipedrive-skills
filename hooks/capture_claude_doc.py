"""PostToolUse hook: save the Claude Docs connector's create, fills and read-back of a doc in .pipedrive-sales/runs/claude-doc.json.

0.7.14 runs (i), (ii) and (a) made a Claude Doc through the connector and nothing saved or verified it; one chat said itself
it had not read the doc back. The 0.7.15 phase 0 probe recorded the shapes (docs/live-runs-0.7.15.md): Cowork names the
tools mcp__Claude_Docs__batch / update / read (Claude Code: mcp__claude_ai_Claude_Docs__…); every response is a one-element
list of text blocks whose text is JSON; the create ack carries created.minted (the doc id), created.bound and frame.url;
every later call names the doc in container.id; a read's JSON carries verdict and rev. The create ack's text is that JSON
followed by a trailer (the doc link for the chat), so the leading JSON object is parsed and the rest is searched for the
link; a create whose ack gave no id adopts the doc id the first fill or read names (0.7.15 check (ii): the ack was not
parsed, the record had no id and every fill was ignored). This hook keeps one record, the
latest doc: its id, link and name from the create, the count and time of the fills, and the latest read-back, so
`artifact_delivery.py verify-doc` can verify "created and read back" and print the one line the chat sends (0.7.15,
decision 3A, ADR-017 note). Only where .pipedrive-sales exists; prints nothing; always exits 0, so a capture problem never
fails the connector call. Finds the folder by walking up from CLAUDE_PROJECT_DIR, then the event's cwd.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

TOOL = re.compile(r"mcp__.*Claude_Docs__(batch|create|update|read|delete|export|query)", re.I)
RECORD = "claude-doc.json"
LINK = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def runs_folder(event):
    for base in (os.environ.get("CLAUDE_PROJECT_DIR"), event.get("cwd"), os.getcwd()):
        if not base:
            continue
        start = Path(base)
        for candidate in (start, *start.parents):
            folder = candidate if candidate.name == ".pipedrive-sales" else candidate / ".pipedrive-sales"
            if (folder / "runs").is_dir():
                return folder / "runs"
    return None


def raw_text(response):
    """The connector's text: the first text block's text (Cowork, Claude Code), a string as given, else the object as JSON."""
    if isinstance(response, dict) and isinstance(response.get("content"), list):
        response = response["content"]
    if isinstance(response, list):
        response = next((block["text"] for block in response if isinstance(block, dict) and isinstance(block.get("text"), str)), "")
    if isinstance(response, str):
        return response
    return json.dumps(response, ensure_ascii=False) if isinstance(response, dict) else ""


def payload(response):
    """The JSON object the connector returned, from the text's first `{` on; whatever follows it (a trailer with the doc
    link, a notice) is left to the link search."""
    text = raw_text(response)
    start = text.find("{")
    if start >= 0:
        try:
            parsed, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    match = TOOL.fullmatch(str(event.get("tool_name") or ""))
    if not match:
        return 0
    runs = runs_folder(event)
    if runs is None:
        return 0
    action = match.group(1).lower()
    if action in ("export", "query"):
        return 0  # reads of the connector that change no doc (matched since 0.7.20 so a new call never goes unseen)
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    container = tool_input.get("container") if isinstance(tool_input.get("container"), dict) else {}
    reference = tool_input.get("ref") if isinstance(tool_input.get("ref"), dict) else {}
    try:
        turn = json.loads((runs / "turn.json").read_text(encoding="utf-8")).get("turn")
    except (OSError, ValueError, AttributeError):
        turn = None
    call = {"tool": str(event.get("tool_name")), "tool_use_id": re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("tool_use_id") or "")),
            "turn": turn, "session_id": event.get("session_id"), "at": now()}
    raw = raw_text(event.get("tool_response"))
    parsed = payload(event.get("tool_response"))
    path = runs / RECORD
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if not isinstance(record, dict):
        record = None
    if action == "batch" and isinstance(container.get("create"), dict):
        # The birth of a doc: a new record replaces any earlier doc's.
        created = parsed.get("created") if isinstance(parsed.get("created"), dict) else {}
        frame = parsed.get("frame") if isinstance(parsed.get("frame"), dict) else {}
        url = frame.get("url") or frame.get("artifactUrl") or next(iter(LINK.findall(raw)), None)
        doc_id = created.get("minted") or frame.get("slug") or next(iter(UUID.findall(url or "")), None) or next(iter(UUID.findall(raw)), None)
        if not url and doc_id and UUID.fullmatch(str(doc_id)):
            url = f"https://claude.ai/code/artifact/{doc_id}"
        lids = parsed.get("lids") if isinstance(parsed.get("lids"), dict) else {}
        record = {"doc_id": doc_id, "url": url, "name": container["create"].get("name"),
                  "created": {**call, "verdict": parsed.get("verdict"), "bound": created.get("bound"), "lids": lids},
                  "updates": {"count": 0, "last_at": None, "last_rev": None}, "read_back": None}
    else:
        if record is None:
            return 0  # a fill or a read before any create in this workspace: nothing to attach it to
        lids = (record.get("created") or {}).get("lids") or {}
        named = {str(container.get("id")), str(reference.get("id"))}
        if not record.get("doc_id"):
            # The create ack gave no id (not parsed, no link): the doc is the one the first fill or read names.
            adopted = next((value for value in (container.get("id"), reference.get("id")) if isinstance(value, str) and UUID.fullmatch(value)), None)
            if adopted is None:
                return 0
            record["doc_id"] = adopted
            record["url"] = record.get("url") or f"https://claude.ai/code/artifact/{adopted}"
        elif str(record["doc_id"]) not in named and not (named & {str(value) for value in lids.values()}):
            return 0  # another doc
        if action == "delete":
            record["deleted"] = {**call, "verdict": parsed.get("verdict")}
        elif action == "read":
            kind = tool_input.get("payload", {}).get("kind") if isinstance(tool_input.get("payload"), dict) else None
            data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
            record["read_back"] = {**call, "verdict": parsed.get("verdict"), "rev": parsed.get("rev", data.get("rev")), "kind": kind}
        else:
            updates = record.get("updates") if isinstance(record.get("updates"), dict) else {}
            record["updates"] = {"count": int(updates.get("count") or 0) + 1, "last_at": call["at"], "last_rev": parsed.get("rev"),
                                 "last_tool_use_id": call["tool_use_id"], "last_verdict": parsed.get("verdict")}
    try:
        temporary = runs / (RECORD + ".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a capture failure must never fail the connector call
        sys.exit(0)
