"""PostToolUse hook: save the host's response to an Artifact publication in .pipedrive-sales/runs/artifact-publish.json.

Every forecast dashboard run in 0.7.12 and 0.7.13 skipped the skill's verify step: the chat was to save the complete host
response to a file and run artifact_delivery.py verify-native on it, and said itself that it had not. The response the
hook sees is the same one, so it saves it (0.7.14, decision 3A, ADR-017 note), and `artifact_delivery.py verify-publish`
reads it, verifies it and prints the one line the chat sends; the Stop hook checks that line. The record keeps the tool's
input and response as given (the artifact link and the host's confirmation line; no CRM data), the turn from runs/turn.json
and the tool_use_id. One file, the latest publication: a second dashboard in the same chat replaces it and gets its own
verify. Only where .pipedrive-sales exists; prints nothing; always exits 0, so a capture problem never fails the publish.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

# The Artifact publish tool as Claude Code names it; a host that namespaces it keeps the word.
TOOL = re.compile(r"(?:mcp__.+__)?Artifact", re.I)


def runs_folder(event):
    """runs/ under the first .pipedrive-sales at or above CLAUDE_PROJECT_DIR, else the event's cwd (phase 0: the publish
    fired with cwd inside .pipedrive-sales/results); None where no folder exists."""
    for base in (os.environ.get("CLAUDE_PROJECT_DIR"), event.get("cwd"), os.getcwd()):
        if not base:
            continue
        start = Path(base)
        for candidate in (start, *start.parents):
            folder = candidate if candidate.name == ".pipedrive-sales" else candidate / ".pipedrive-sales"
            if (folder / "runs").is_dir():
                return folder / "runs"
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict) or not TOOL.fullmatch(str(event.get("tool_name") or "")):
        return 0
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    response = event.get("tool_response")
    # The same tool opens, lists, reads and pins artifacts (0.7.14 phase 0: an `open` of the Claude Doc fired first). Only a
    # publication is saved: a file published and a response naming its path and version.
    if "action" in tool_input or "file_path" not in tool_input:
        return 0
    if isinstance(response, dict) and not ("path" in response or "version" in response or "content" in response
                                           or "structured_content" in response or response.get("is_error")):
        return 0
    runs = runs_folder(event)
    if runs is None:
        return 0
    try:
        turn = json.loads((runs / "turn.json").read_text(encoding="utf-8")).get("turn")
    except (OSError, ValueError, AttributeError):
        turn = None
    record = {"tool": str(event.get("tool_name")), "tool_use_id": re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("tool_use_id") or "")),
              "turn": turn, "session_id": event.get("session_id"),
              "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "input": {key: value for key, value in tool_input.items() if not key.startswith("__")},
              "response": response}
    try:
        temporary = runs / "artifact-publish.json.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, runs / "artifact-publish.json")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a capture failure must never fail the publication
        sys.exit(0)
