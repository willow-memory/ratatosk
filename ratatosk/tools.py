"""Tool definitions and dispatch for the Ratatosk tool loop."""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from ratatosk.capabilities import ActionResult, CapabilityGate
from ratatosk.protocol.envelope import Intent, build_envelope

BASH_TOOL = {
    "name": "Bash",
    "description": (
        "Run a command without a shell (POSIX words via shlex). "
        "High-risk — requires confirmation unless --trust."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
        },
        "required": ["command"],
    },
}

READ_TOOL = {
    "name": "Read",
    "description": "Read a file from disk and return its contents (up to 4000 chars).",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
        },
        "required": ["file_path"],
    },
}

WRITE_TOOL = {
    "name": "Write",
    "description": "Write content to a file. Creates parent directories if needed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["file_path", "content"],
    },
}

EDIT_TOOL = {
    "name": "Edit",
    "description": "Replace old_string with new_string in a file.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}

GLOB_TOOL = {
    "name": "Glob",
    "description": "List files matching a glob pattern.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "cwd": {"type": "string"},
        },
        "required": ["pattern"],
    },
}

BASE_TOOLS = [BASH_TOOL, READ_TOOL, WRITE_TOOL, EDIT_TOOL, GLOB_TOOL]

_MAX_READ = 4000
_BASH_TIMEOUT = 60
_GATE = CapabilityGate()


def _bash_allowed(command: str, trusted: bool) -> tuple[bool, str]:
    if trusted:
        return True, ""
    env = build_envelope(
        to="local",
        prompt=command,
        intent=Intent.SHELL.value,
        requires_confirm=not trusted,
    )
    action = _GATE.classify(env)
    if action == ActionResult.REJECTED:
        return False, "shell blocked by capability gate — use --trust or confirm"
    if action == ActionResult.QUEUED_CONFIRM and not trusted:
        return False, "shell requires confirmation"
    return True, ""


def dispatch(name: str, inputs: dict, mcp_names: set, mcp_call, *, trusted: bool = False) -> object:
    if name == "Bash":
        cmd = inputs.get("command", "")
        allowed, reason = _bash_allowed(cmd, trusted)
        if not allowed:
            return json.dumps({"error": reason})
        try:
            argv = shlex.split(cmd, posix=True)
        except ValueError as exc:
            return f"ERROR: could not parse command: {exc}"
        if not argv:
            return "(no command)"
        try:
            result = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=_BASH_TIMEOUT,
            )
            return (result.stdout + result.stderr).strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out ({_BASH_TIMEOUT}s)"
        except Exception as exc:
            return f"ERROR: {exc}"

    if name == "Read":
        path = inputs.get("file_path", "") or inputs.get("path", "")
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")[:_MAX_READ]
        except Exception as exc:
            return f"ERROR: {exc}"

    if name == "Write":
        path = inputs.get("file_path", "")
        content = inputs.get("content", "")
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Written {len(content)} chars to {path}"
        except Exception as exc:
            return f"ERROR: {exc}"

    if name == "Edit":
        path = inputs.get("file_path", "")
        old = inputs.get("old_string", "")
        new = inputs.get("new_string", "")
        try:
            text = Path(path).read_text(encoding="utf-8")
            count = text.count(old)
            if count == 0:
                return f"ERROR: old_string not found in {path}"
            if count > 1:
                return f"ERROR: old_string matches {count} times — must be unique"
            Path(path).write_text(text.replace(old, new, 1), encoding="utf-8")
            return f"Edited {path}"
        except Exception as exc:
            return f"ERROR: {exc}"

    if name == "Glob":
        import glob as _glob

        pattern = inputs.get("pattern", "")
        cwd = inputs.get("cwd", "") or str(Path.cwd())
        try:
            matches = sorted(_glob.glob(pattern, root_dir=cwd, recursive=True))
            return "\n".join(matches) if matches else "(no matches)"
        except Exception as exc:
            return f"ERROR: {exc}"

    if name in mcp_names and mcp_call is not None:
        return mcp_call(name, inputs)

    return f"[stub] tool '{name}' not wired. inputs={json.dumps(inputs)[:120]}"


def prompt_and_dispatch(
    name: str,
    inputs: dict,
    trusted: bool,
    mcp_names: set,
    mcp_call,
) -> object:
    if not trusted:
        summary = json.dumps(inputs, ensure_ascii=False)[:200]
        print(f"\n  [tool:{name}] {summary}")
        try:
            answer = input("  Allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "y":
            return json.dumps({"error": "user denied"})
    return dispatch(name, inputs, mcp_names, mcp_call, trusted=trusted)
