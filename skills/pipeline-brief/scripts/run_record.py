"""Truthful run-file call records for Pipeline Brief."""

from copy import deepcopy


def append_call(run, tool, params, response, elapsed_ms, response_file=None):
    """Append one MCP call without reconstructing its request parameters."""
    call = {
        "tool": tool,
        "params": deepcopy(params),
        "elapsed_ms": elapsed_ms,
    }
    if response_file is None:
        call["response"] = response
    else:
        call["response_file"] = response_file
    run.setdefault("calls", []).append(call)
    return call
