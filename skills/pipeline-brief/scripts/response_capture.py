"""Retain complete MCP JSON beside a run and distinguish original bytes from inline copies."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone


def retain_response(run_path, raw, tool, params, observed_at=None, tool_use_id=None, capture_kind="original_bytes",
                    source_sha256=None):
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Expected a complete JSON response object")
    run_path = Path(run_path).resolve()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    if tool_use_id and any(item.get("tool_use_id") == tool_use_id for item in run.get("calls", [])):
        raise ValueError("This tool response is already recorded")
    digest = hashlib.sha256(raw).hexdigest()
    directory = run_path.parent / "responses"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{digest}.json"
    destination.write_bytes(raw)
    call = {"tool": tool, "params": params, "response_file": str(destination.relative_to(run_path.parent)),
            "capture_kind": capture_kind, "original_bytes_retained": capture_kind == "original_bytes"}
    if isinstance(payload.get("data"), list):
        call["retained_record_count"] = len(payload["data"])
    if observed_at:
        call["observed_at"] = observed_at
    if tool_use_id:
        call["tool_use_id"] = tool_use_id
    if source_sha256:
        call["source_sha256"] = source_sha256  # the plugin hook's hash of the text it received
    run.setdefault("calls", []).append(call)
    temporary = run_path.with_suffix(run_path.suffix + ".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run_path)
    return call


def transcript_response(document, tool_use_id):
    blocks = []
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") in {"tool_use", "tool_result"}:
                blocks.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(document)
    requests = [item for item in blocks if item.get("type") == "tool_use" and item.get("id") == tool_use_id]
    responses = [item for item in blocks if item.get("type") == "tool_result" and item.get("tool_use_id") == tool_use_id]
    if len(requests) != 1 or len(responses) != 1:
        raise ValueError("Need exactly one original request and complete response for the tool-use ID")
    content = responses[0].get("content")
    if isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, list) and len(content) == 1 and content[0].get("type") == "text":
        raw = content[0]["text"].encode("utf-8")
    else:
        raise ValueError("Response is not a single complete JSON text block; use the original response file")
    json.loads(raw)
    request = requests[0]
    if not isinstance(request.get("input"), dict) or not request.get("name"):
        raise ValueError("Original request parameters are unavailable")
    return raw, request["name"], request["input"]


def relocate(run_path):
    run_path = Path(run_path).resolve()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    copies = []
    for call in run.get("calls", []):
        if call.get("response_file"):
            source = Path(call["response_file"])
            if not source.is_absolute():
                source = run_path.parent / source
            raw = source.read_bytes()
            json.loads(raw)
            copies.append((call, raw))
    directory = run_path.parent / "responses"
    directory.mkdir(parents=True, exist_ok=True)
    for call, raw in copies:
        destination = directory / f"{hashlib.sha256(raw).hexdigest()}.json"
        destination.write_bytes(raw)
        call["response_file"] = str(destination.relative_to(run_path.parent))
    temporary = run_path.with_suffix(run_path.suffix + ".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run_path)
    return len(copies)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--response-file", help="Original JSON response file, or - for original JSON bytes on stdin")
    source.add_argument("--transcript", help="Host-exported transcript containing the original request and response")
    source.add_argument("--inline-json", help="Complete visible connector JSON copied unchanged to a file; not a wire-byte archive")
    source.add_argument("--relocate", action="store_true", help="Copy all existing response files beside this run")
    parser.add_argument("--tool")
    parser.add_argument("--params-file", help="JSON containing the exact request parameters")
    parser.add_argument("--tool-use-id")
    parser.add_argument("--observed-at", help="Actual response observation time, if recorded by the host")
    parser.add_argument("--observed-now", action="store_true",
                        help="Record observation time for a response just received from a live tool call; never use on a replay")
    args = parser.parse_args(argv)
    if args.observed_at and args.observed_now:
        parser.error('Choose the original observation timestamp or a just-observed live response')
    if args.relocate:
        print(f"Retained {relocate(args.run)} original response files")
        return 0
    if args.transcript:
        if not args.tool_use_id:
            parser.error("--transcript requires --tool-use-id")
        raw, tool, params = transcript_response(json.loads(Path(args.transcript).read_text(encoding="utf-8")), args.tool_use_id)
    else:
        if not args.tool or not args.params_file:
            parser.error("Response capture requires --tool and --params-file")
        raw = (Path(args.inline_json).read_bytes() if args.inline_json else
               sys.stdin.buffer.read() if args.response_file == "-" else Path(args.response_file).read_bytes())
        tool, params = args.tool, json.loads(Path(args.params_file).read_text(encoding="utf-8"))
        if not isinstance(params, dict):
            parser.error("Request parameters must be a JSON object")
    kind = "inline_json" if args.inline_json else "original_bytes"
    observed_at = datetime.now(timezone.utc).isoformat() if args.observed_now else args.observed_at
    call = retain_response(args.run, raw, tool, params, observed_at, args.tool_use_id, kind)
    print(json.dumps(call, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
