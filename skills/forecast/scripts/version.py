#!/usr/bin/env python3
"""Skill name and release version for the skill folder this file sits in (S9), plus the tester footer line (S8).

SOURCE OF TRUTH: shared/version.py. Every skill folder carries a byte-identical copy (tools/sync-shared.py).
It reads `name` and `metadata.version` from the SKILL.md next to scripts/, so a copy never reports another
skill's version. read() and info() are strict (the build and tests use them); stamp() stamps "unknown" when the
version cannot be read, so a computed report is never lost at its last step. Every saved result carries stamp(); a rep-facing output ends with tester_line() only when the
effective settings turn "tester_footer" on.

    python3 scripts/version.py        # {"build_id": "...", "skill_version": "..."} (+ plugin_version where aligned)
"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")  # 0.7.1 or a pre-release such as 0.7.1-rc1
PLUGIN_ALIGNED = ("pipeline-brief", "pipedrive-kitchen", "deal-triage", "forecast", "meeting-follow-up",
                  "sales-outreach")  # since 0.7.20 every skill's version equals the plugin manifest's (ADR-048 note)


def _semantic_version(value, surface):
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise ValueError(f"{surface} must contain a semantic version")
    return value


def _unquoted(value):
    """Cowork re-serialises the installed SKILL.md frontmatter with quotes (`version: '0.7.0'`); strip one matching pair."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _fields(folder):
    """(name, raw version) as written in the SKILL.md frontmatter; either is None when absent. Reads block metadata
    (`  version: 0.7.1`) and flow metadata (`metadata: { author: x, version: 0.7.1 }`), quoted or not."""
    skill = (folder / "SKILL.md").read_text(encoding="utf-8")
    block = re.match(r"^---\r?\n(.*?)\r?\n---", skill, re.DOTALL)
    frontmatter = block.group(1) if block else skill
    name = re.search(r"^name:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    version = re.search(r"^[ \t]+version:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    if not version:
        flow = re.search(r"^metadata:\s*\{(.*?)\}", frontmatter, re.MULTILINE | re.DOTALL)
        version = flow and re.search(r"""(?:^|[,{\s])["']?version["']?\s*:\s*("[^"]*"|'[^']*'|[^,}\s]+)""", flow.group(1))
    raw = version.group(1) if version else None
    if raw is not None and raw[:1] not in "'\"":
        raw = re.sub(r"\s+#.*$", "", raw)
    return (_unquoted(name.group(1)) if name else folder.name), raw and _unquoted(raw)


def read(folder=None):
    """Return (skill name, metadata.version) from the SKILL.md in `folder` (default: this file's skill folder).
    Strict: the build and the shipped-skill tests use it, so a missing or non-semantic version raises."""
    folder = Path(folder) if folder else FOLDER
    name, version = _fields(folder)
    if version is None:
        raise ValueError(f"{folder.name} metadata must contain a version")
    return name, _semantic_version(version, f"{folder.name} metadata")


def info(folder=None):
    """The release versions this folder reports; plugin_version is checked against skill_version where aligned."""
    name, version = read(folder)
    result = {"skill_version": version, "build_id": f"{name}-{version}"}
    if name in PLUGIN_ALIGNED:
        manifest = ROOT / ".claude-plugin" / "plugin.json"
        plugin = _semantic_version(json.loads(manifest.read_text(encoding="utf-8")).get("version"), "plugin manifest") if manifest.exists() else None
        if plugin is not None and version != plugin:
            raise ValueError(f"{name} metadata and plugin manifest versions differ; the plugin manifest is the release version source")
        result["plugin_version"] = plugin
    return result


def stamp(calls, estimated_tokens, folder=None):
    """What every saved result carries: skill, version, real reads (pdcore.collect's tool_calls) and estimated tokens."""
    try:
        name, version = read(folder)
    except (ValueError, OSError):
        # The end of a computed report never fails on a frontmatter shape this reader does not know; the stamp says so.
        folder = Path(folder) if folder else FOLDER
        try:
            name = _fields(folder)[0]
        except OSError:
            name = folder.name
        version = "unknown"
    return {"skill": name, "version": version, "calls": calls, "estimated_tokens": estimated_tokens}


def tester_line(result):
    """The opt-in tester footer for a stamped result; None when the result has no stamp."""
    if not all(key in result for key in ("skill", "version", "calls", "estimated_tokens")):
        return None
    return f"Tester: {result['skill']} {result['version']} · {result['calls']} call{'' if result['calls'] == 1 else 's'} · ~{result['estimated_tokens']:,} tokens of Pipedrive data read by the scripts (estimated)"


if __name__ == "__main__":
    print(json.dumps(info(), sort_keys=True))
