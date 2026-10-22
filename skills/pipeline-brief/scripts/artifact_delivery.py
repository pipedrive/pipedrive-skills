"""Observed client capabilities and safe artifact delivery selection.

Since 0.7.14 (decision 3A, ADR-017 note) `verify-publish` verifies the publication the plugin hook saved
(hooks/capture_artifact_publish.py writes .pipedrive-sales/runs/artifact-publish.json) and prints the one line the chat
sends, saved as the turn's checked reply where no unchecked reply exists yet. `verify-native --host-result` stays for a
host without plugin hooks (claude.ai chat), where the chat saves the response itself. Since 0.7.15 (decision 3A, ADR-017
note) `verify-doc` does the same for a Claude Doc: hooks/capture_claude_doc.py saves the connector's create, fills and
read-back in .pipedrive-sales/runs/claude-doc.json, and the line is "Claude Doc created and read back: <link>" only when
the doc was created and read back after its last fill; on a Both turn the line follows the dashboard line in one checked
reply."""

import hashlib
import json
import os
from pathlib import Path
import argparse
import re
import sys


DEFAULT_CAPABILITIES = {
    "python": False,
    "persistent_files": False,
    "local_html": False,
    "native_artifact": False,
    "native_artifact_canonical_html": False,
    "native_artifact_storage_notice": None,
    "wait": False,
    "activity_owner_assignment": False,
    "mail_draft": False,
}


def load_profile(path, client, build, runtime_probe):
    """Load only a dated, client-specific validation for this build/session."""
    try:
        registry = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_CAPABILITIES)
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        return dict(DEFAULT_CAPABILITIES)
    for record in registry.get("profiles", []):
        if not isinstance(record, dict) or record.get("client") != client:
            continue
        if any(not isinstance(record.get(key), str) or not record[key].strip()
               for key in ("profile_version", "evidence_id", "validated_at")):
            continue
        if record.get("build_unobservable") is True:
            if not isinstance(runtime_probe, dict) or runtime_probe.get("current_session") is not True:
                continue
            permitted = {key: record.get(key) is True and runtime_probe.get(key) is True
                         for key in DEFAULT_CAPABILITIES if key != "native_artifact_storage_notice"}
            storage_confirmed = runtime_probe.get("native_artifact_storage_notice") is True
        elif (isinstance(build, str) and build and build == record.get("build")
              and record.get("build_unobservable") is False):
            permitted = {key: record.get(key) is True for key in DEFAULT_CAPABILITIES
                         if key != "native_artifact_storage_notice"}
            storage_confirmed = True
        else:
            continue
        notice = record.get("native_artifact_storage_notice")
        permitted["native_artifact_storage_notice"] = (
            notice if isinstance(notice, str) and notice.strip()
            and permitted["native_artifact"] and permitted["native_artifact_canonical_html"]
            and storage_confirmed else None
        )
        return permitted
    return dict(DEFAULT_CAPABILITIES)


def observed_probe(artifact_tool, local_folder):
    """The current session's probe, from what the chat can see: whether an Artifact publish tool is present now, and
    the user's connected working folder, if any. How the host publishes (supplied HTML unchanged, the storage notice)
    comes from the validated profile, never from the chat's reading of the tool: two 0.7.9 Cowork forecast runs ten
    minutes apart judged it differently, and one refused the dashboard (0.7.10). The folder is tested here, by a
    write and a read."""
    native = artifact_tool is True
    return {"current_session": True, "native_artifact": native, "native_artifact_canonical_html": native,
            "native_artifact_storage_notice": native, "local_html": bool(local_folder) and folder_writable(local_folder)}


def folder_writable(folder):
    marker = Path(folder) / f".pipedrive-sales-probe-{os.getpid()}"
    try:
        marker.write_text("probe", encoding="utf-8")
        return marker.read_text(encoding="utf-8") == "probe"
    except OSError:
        return False
    finally:
        try:
            marker.unlink()
        except OSError:
            pass


def verify_local_artifact(command_exit, path, expected_sha256, path_absent_before):
    """Accept a local artifact only when a new file has the canonical bytes."""
    result = {"status": "failed", "path": None, "reason": None}
    if command_exit != 0:
        result["reason"] = "The renderer failed."
    elif path_absent_before is not True:
        result["reason"] = "The output path existed before this request."
    else:
        output = Path(path)
        try:
            content = output.read_bytes()
        except OSError:
            content = b""
        if not content:
            result["reason"] = "The renderer did not create a nonempty file."
        elif hashlib.sha256(content).hexdigest() != expected_sha256:
            result["reason"] = "The created file differs from the canonical dashboard."
        else:
            return {"status": "created", "path": str(output.resolve()), "reason": None}
    return result


def verify_native_artifact(host_result):
    """Accept only an explicit host success with an artifact reference."""
    if isinstance(host_result, dict) and (host_result.get("is_error") is True or host_result.get("isError") is True):
        return {"status": "failed", "handle": None, "reason": "The host reported an artifact creation error."}
    if isinstance(host_result, dict) and host_result.get("is_error") is not True:
        # Cowork's Artifact tool answers a file-path publication with a structured object (0.7.14 phase 0, 19 Oct):
        # url, path, artifact_id, title, version, seq, owned, audience, warnings. The url is the handle.
        url, artifact_id, path = host_result.get("url"), host_result.get("artifact_id"), host_result.get("path")
        if (isinstance(url, str) and re.fullmatch(r"https://claude\.ai/artifact/[A-Za-z0-9_-]+", url)
                and isinstance(artifact_id, str)
                and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", artifact_id)
                and isinstance(path, str) and path.strip()
                and host_result.get("owned") is True and "version" in host_result):
            return {"status": "created", "handle": url, "reason": None}
        structured = host_result.get("structured_content")
        if isinstance(structured, dict):
            path = structured.get("path")
            url = structured.get("url")
            artifact_id = structured.get("artifact_id")
            content = host_result.get("content")
            if (host_result.get("message") == "Published artifact"
                    and isinstance(path, str) and path.startswith("/mnt/user-data/outputs/")
                    and isinstance(url, str)
                    and re.fullmatch(r"https://claude\.ai/artifact/[A-Za-z0-9_-]+", url)
                    and isinstance(artifact_id, str)
                    and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", artifact_id)
                    and isinstance(content, list)
                    and any(isinstance(item, dict) and item.get("type") == "text"
                            and item.get("text") == f"Published {path} at {url}"
                            for item in content)):
                return {"status": "created", "handle": url, "reason": None}
        if structured is not None or "message" in host_result:
            return {"status": "failed", "handle": None,
                    "reason": "The structured host publication response did not match its confirmation."}
    if isinstance(host_result, str):
        messages = [host_result]
    elif isinstance(host_result, dict) and isinstance(host_result.get("content"), list):
        messages = [item.get("text") for item in host_result["content"]
                    if isinstance(item, dict) and item.get("type") == "text"]
    else:
        messages = []
    for message in messages:
        if not isinstance(message, str):
            continue
        first_line = message.strip().splitlines()[0] if message.strip() else ""
        published = re.fullmatch(
            r'Published .+ at (https://claude\.ai/artifact/[A-Za-z0-9_-]+) '
            r'\(Version [1-9][0-9]*(?:, version id [0-9]+-[0-9a-fA-F]+)?\)(?: Icon: "[^"]*"\.)?', first_line)
        if published:
            return {"status": "created", "handle": published.group(1), "reason": None}
        claude_ai = re.fullmatch(
            r'Published /mnt/user-data/outputs/[^\s]+ at '
            r'(https://claude\.ai/artifact/[A-Za-z0-9_-]+)', first_line)
        if claude_ai:
            return {"status": "created", "handle": claude_ai.group(1), "reason": None}
    result = host_result if isinstance(host_result, dict) else {}
    handle = result.get("handle") or result.get("url") or result.get("link")
    if result.get("success") is True and isinstance(handle, str) and handle.strip():
        return {"status": "created", "handle": handle, "reason": None}
    return {"status": "failed", "handle": None,
            "reason": "The host did not confirm a created artifact with a handle or link."}


def resolve_capabilities(client, records):
    """Return only observed capabilities for one client."""
    record = (records or {}).get(client)
    if not isinstance(record, dict):
        return dict(DEFAULT_CAPABILITIES)
    resolved = {
        key: record.get(key) is True
        for key in DEFAULT_CAPABILITIES
        if key != "native_artifact_storage_notice"
    }
    notice = record.get("native_artifact_storage_notice")
    resolved["native_artifact_storage_notice"] = (
        notice if isinstance(notice, str) and notice.strip()
        and resolved["native_artifact"]
        and resolved["native_artifact_canonical_html"] else None
    )
    return resolved


def delivery_options(result, capabilities):
    """Return blocked, unsupported, one available option, or a user-choice list."""
    capabilities = capabilities or {}
    text_brief = result.get("markdown") or "Brief unavailable"
    coverage = result.get("coverage_line") or "Coverage unavailable"
    response = {"path": None, "published": False, "text_brief": text_brief, "coverage": coverage}
    if result.get("data_unavailable"):
        return {**response, "status": "blocked",
                "reason": "Artifact unavailable while required data is unavailable."}
    is_triage = (result.get("report_skill") == "deal-triage" or (result.get("report_skill") is None and result.get("mode") == "triage" and ("proposals" in result or ("aggregates" not in result and "ranked" not in result))))
    execution = result.get("rule_execution")
    if is_triage and not isinstance(execution, dict):
        unavailable = ["R1", "R2"]
    else:
        unavailable = []
        for rule in ("R1", "R2"):
            state = execution.get(rule) if isinstance(execution, dict) else None
            if is_triage and (
                not isinstance(state, dict) or type(state.get("unavailable")) is not int
                or state["unavailable"] < 0
            ):
                unavailable.append(rule)
            elif isinstance(state, dict) and state.get("unavailable", 0):
                unavailable.append(rule)
    if result.get('report_schema_version') == 2:
        from report_contract import dashboard_allowed
        if not dashboard_allowed(result):
            return {**response, 'status': 'blocked', 'reason': 'Required report evidence is unavailable.'}
        unavailable = []
    if unavailable:
        return {**response, "status": "blocked",
                "reason": "Artifact unavailable while " + ", ".join(unavailable) + " evidence is unavailable."}

    options = []
    notice = capabilities.get("native_artifact_storage_notice")
    if (capabilities.get("native_artifact") is True
            and capabilities.get("native_artifact_canonical_html") is True
            and isinstance(notice, str) and notice.strip()):
        options.append({"id": "native_artifact", "label": "Claude dashboard", "storage_notice": notice})
    if capabilities.get("local_html") is True:
        options.append({"id": "local_html", "label": "Private local HTML"})

    if not options:
        return {**response, "status": "unsupported", "options": []}
    if len(options) == 1:
        return {**response, "status": "available", "options": options}
    return {**response, "status": "choice_required", "options": options}


PUBLISH_RECORD = "artifact-publish.json"


def resolve_runs(given):
    """The runs folder: the given path when it exists, else the first .pipedrive-sales/runs at or above the working folder.
    The chat's shell may sit inside .pipedrive-sales/results when it publishes (0.7.14 phase 0; 0.7.15 check (ii), where
    both verify commands then saw no record and the chat fell back to saving the host response itself)."""
    path = Path(given)
    if path.is_dir():
        return path
    start = Path.cwd()
    for candidate in (start, *start.parents):
        folder = candidate if candidate.name == ".pipedrive-sales" else candidate / ".pipedrive-sales"
        if (folder / "runs").is_dir():
            return folder / "runs"
    return path


def verify_publication(runs, turn=None):
    """Verify the publication the plugin hook saved in runs/artifact-publish.json for this turn.

    Returns the verify result with `line`, the sentence the chat sends, for a created or failed publication; or a
    `missing` / `stale` status with `reason` for the chat alone (no publication saved; one from an earlier message)."""
    runs = Path(runs)
    try:
        record = json.loads((runs / PUBLISH_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "missing", "handle": None, "line": None,
                "reason": "No publication is saved for this workspace: publish the validated file with the Artifact tool first "
                          "(the plugin hook saves the host's response), or on a host without plugin hooks save that response "
                          "yourself and run verify-native --host-result."}
    if not isinstance(record, dict):
        return {"status": "missing", "handle": None, "line": None, "reason": "The saved publication record is not an object."}
    if turn is None:
        try:
            turn = json.loads((runs / "turn.json").read_text(encoding="utf-8")).get("turn")
        except (OSError, ValueError, AttributeError):
            turn = None
    if turn and record.get("turn") and record.get("turn") != turn:
        return {"status": "stale", "handle": None, "line": None,
                "reason": "The saved publication is from an earlier message. Publish this turn's file first, then verify."}
    result = verify_native_artifact(record.get("response"))
    if result["status"] == "created":
        result["line"] = f"Dashboard published and verified: {result['handle']}"
    else:
        result["line"] = (f"The dashboard could not be verified from the host's confirmation ({result['reason'].rstrip('.')}). "
                          "Treat any link above as unconfirmed.")
    result["tool_use_id"] = record.get("tool_use_id")
    return result


DOC_RECORD = "claude-doc.json"


DOC_OUTLINE = "doc-outline.json"


def doc_outline(result):
    """The Claude Doc's fixed title and headings for a saved result (0.7.19 decision 7A: three runs from the same data
    gave three titles and three outlines). The chat fills the sections; the shape is the script's."""
    skill = str(result.get("report_skill") or result.get("skill") or "report")
    period = str(result.get("period") or result.get("today") or "")
    scope = str(result.get("scope_label") or "").strip()
    label = {"forecast": "Revenue forecast", "pipeline-brief": "Pipeline brief", "deal-triage": "Deals needing attention"}.get(skill, skill)
    title = " — ".join(part for part in (label, period) if part) + (f" ({scope})" if scope else "")
    currencies = result.get("currency_sections") if isinstance(result.get("currency_sections"), dict) else {}
    headings = ["Summary", "Scope and coverage"]
    headings += [f"{code} forecast" for code in currencies] if skill == "forecast" else ["Findings"]
    headings += ["Largest included deals", "Risks and assumptions", "Next steps"] if skill == "forecast" else ["Next steps"]
    return {"title": title, "headings": headings}


def plan_document(runs, result):
    """Save the planned title and headings for this turn (runs/doc-outline.json) and return them; verify-doc holds the
    created doc to the title."""
    runs = Path(runs)
    try:
        turn = json.loads((runs / "turn.json").read_text(encoding="utf-8")).get("turn")
    except (OSError, ValueError, AttributeError):
        turn = None
    outline = {**doc_outline(result), "turn": turn}
    try:
        runs.mkdir(exist_ok=True)
        (runs / DOC_OUTLINE).write_text(json.dumps(outline, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return outline


def planned_outline(runs, turn):
    try:
        outline = json.loads((Path(runs) / DOC_OUTLINE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(outline, dict) or (turn and outline.get("turn") and outline.get("turn") != turn):
        return None
    return outline


def verify_document(runs, turn=None):
    """Verify the Claude Doc the plugin hook saved in runs/claude-doc.json: created (bound, with a link) and read back after
    its last fill, this turn. Returns `line`, the sentence the chat sends, for a verified or failed doc; a `missing`, `stale`,
    `not_read_back` or `read_before_fill` status with `reason` is for the chat alone (it has a step to do first)."""
    runs = Path(runs)
    try:
        record = json.loads((runs / DOC_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "missing", "handle": None, "line": None,
                "reason": "No Claude Doc is saved for this workspace: create it with the Claude Docs tools first (the plugin hook "
                          "saves the connector's responses). On a host without plugin hooks report the connector's returned link "
                          "and your own read-back instead."}
    if not isinstance(record, dict) or not isinstance(record.get("created"), dict):
        return {"status": "missing", "handle": None, "line": None, "reason": "The saved Claude Doc record is not a create record."}
    created, read = record["created"], record.get("read_back") if isinstance(record.get("read_back"), dict) else None
    if turn is None:
        try:
            turn = json.loads((runs / "turn.json").read_text(encoding="utf-8")).get("turn")
        except (OSError, ValueError, AttributeError):
            turn = None
    touched = {created.get("turn"), (record.get("updates") or {}).get("turn"), (read or {}).get("turn")} - {None}
    if turn and touched and turn not in touched:
        return {"status": "stale", "handle": None, "line": None,
                "reason": "The saved Claude Doc is from an earlier message. Create or read back this turn's doc first, then verify."}
    url = record.get("url")
    failure = None
    if created.get("verdict") not in (None, "allow"):
        failure = f"the connector answered {created.get('verdict')!r} to the create"
    elif created.get("bound") is False:
        failure = "the connector made the doc without binding a viewer, so there is no link to open"
    elif not url or not url.startswith("https://claude.ai/"):
        failure = "the create ack carries no claude.ai link"
    if failure:
        return {"status": "failed", "handle": url, "doc_id": record.get("doc_id"), "reason": failure,
                "line": f"The Claude Doc could not be verified from the host's confirmation ({failure}). Treat any link above as unconfirmed."}
    outline = planned_outline(runs, turn)
    if outline and outline.get("title") and str(record.get("name") or "").strip() != outline["title"]:
        return {"status": "retitle", "handle": url, "doc_id": record.get("doc_id"), "line": None,
                "reason": f"The doc's title is not the planned one. Rename it to exactly: {outline['title']} (the Claude Docs "
                          "rename update), read it back, then run verify-doc again."}
    last_fill = (record.get("updates") or {}).get("last_at") or created.get("at")
    if read is None or read.get("verdict") not in (None, "allow"):
        return {"status": "not_read_back", "handle": url, "doc_id": record.get("doc_id"), "line": None,
                "reason": "The doc was created but not read back: read its body with the Claude Docs read tool (the doc's node, "
                          "kind view or outline) after the last fill, then run verify-doc again."}
    if last_fill and read.get("at") and str(read["at"]) < str(last_fill):
        return {"status": "read_before_fill", "handle": url, "doc_id": record.get("doc_id"), "line": None,
                "reason": "The doc changed after the last read-back: read it back again after the last fill, then run verify-doc again."}
    return {"status": "created", "handle": url, "doc_id": record.get("doc_id"), "reason": None,
            "line": f"Claude Doc created and read back: {url}", "tool_use_id": created.get("tool_use_id"), "read_back_rev": read.get("rev")}


def _save_checked_line(runs, line, kind="dashboard"):
    """Save the line as the turn's checked reply unless a reply saved this turn is still unchecked (the report that offered
    the dashboard, when the rep's choice did not start a new turn); then the line follows that reply as the chat's own text.
    A doc line after this turn's unsent dashboard line (Both) joins it: one checked reply, the dashboard line first (0.7.15).
    Returns the outcome, the framing function and the text to send."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pdcore
    runs = Path(runs)
    try:
        turn = json.loads((runs / "turn.json").read_text(encoding="utf-8")).get("turn")
    except (OSError, ValueError, AttributeError):
        turn = None
    try:
        expected = json.loads((runs / "expected-reply.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        expected = None
    if isinstance(expected, dict) and turn and expected.get("turn") == turn and not expected.get("checked"):
        if kind == "doc" and expected.get("kind") == "dashboard" and isinstance(expected.get("text"), str):
            combined = expected["text"].rstrip() + "\n\n" + line
            saved = pdcore.save_expected_reply(combined, "both", near=runs)
            return ("saved_after_dashboard" if saved else "not_saved"), pdcore.framed, combined
        return "kept_earlier", pdcore.framed, line
    saved = pdcore.save_expected_reply(line, kind, near=runs)
    return ("saved" if saved else "not_saved"), pdcore.framed, line


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    profile = actions.add_parser("profile")
    profile.add_argument("--client", required=True)
    profile.add_argument("--build")
    profile.add_argument("--artifact-tool", choices=("yes", "no"))
    profile.add_argument("--local-folder")
    local = actions.add_parser("verify-local")
    local.add_argument("--exit-code", required=True, type=int)
    local.add_argument("--path", required=True)
    local.add_argument("--sha256", required=True)
    local.add_argument("--absent-before", action="store_true")
    native = actions.add_parser("verify-native")
    native.add_argument("--host-result", required=True)
    publish = actions.add_parser("verify-publish", help="verify the publication the plugin hook saved and print the line to send")
    publish.add_argument("--runs", default="./.pipedrive-sales/runs", help="the runs folder holding artifact-publish.json")
    document = actions.add_parser("verify-doc", help="verify the Claude Doc the plugin hook saved (created and read back) and print the line to send")
    document.add_argument("--runs", default="./.pipedrive-sales/runs", help="the runs folder holding claude-doc.json")
    outline = actions.add_parser("doc-outline", help="print the Claude Doc's fixed title and headings for the saved result and save them for verify-doc")
    outline.add_argument("--input", required=True, help="the saved result")
    outline.add_argument("--runs", default="./.pipedrive-sales/runs")
    args = parser.parse_args(argv)
    if args.action == "doc-outline":
        result = json.loads(Path(args.input).read_text(encoding="utf-8"))
        print(json.dumps(plan_document(resolve_runs(args.runs), result), ensure_ascii=False, indent=2))
        return 0
    if args.action in ("verify-publish", "verify-doc"):
        runs = resolve_runs(args.runs)
        result = verify_publication(runs) if args.action == "verify-publish" else verify_document(runs)
        if result["line"] is None:
            print(json.dumps(result, sort_keys=True))
            return 1
        result["reply_check"], framed, text = _save_checked_line(runs, result["line"], "dashboard" if args.action == "verify-publish" else "doc")
        if result["reply_check"] == "kept_earlier":
            print("Send the checked reply saved earlier this turn first, then this line after it:")
            print(result["line"])
        elif result["reply_check"] == "saved_after_dashboard":
            print("The dashboard line saved this turn comes first; send both lines between the markers as the reply:")
            print(framed(text))
        else:
            print(framed(text))
        return 0 if result["status"] == "created" else 1
    if args.action == "profile":
        probe = (observed_probe(args.artifact_tool == "yes", args.local_folder)
                 if args.artifact_tool or args.local_folder else None)
        result = load_profile(Path(__file__).with_name("client_capabilities.json"), args.client, args.build, probe)
    elif args.action == "verify-local":
        result = verify_local_artifact(args.exit_code, args.path, args.sha256, args.absent_before)
    else:
        raw = Path(args.host_result).read_text(encoding="utf-8")
        try:
            host_result = json.loads(raw)
        except ValueError:
            host_result = raw
        result = verify_native_artifact(host_result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status", "created") == "created" else 1


if __name__ == "__main__":
    raise SystemExit(main())
