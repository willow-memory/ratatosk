"""Tool definitions and dispatch for the Ratatosk tool loop."""
from __future__ import annotations

import json
import shlex
import subprocess
import uuid
from pathlib import Path

from ratatosk.capabilities import CapabilityGate
from ratatosk.child_env import child_env
from ratatosk.permission import NeedsConfirmation, Verdict, check
from ratatosk.redact import redact
from ratatosk.hooks import HookRuntime
from ratatosk.policy import PolicyStore

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


def _gate_for(gate: CapabilityGate | None) -> CapabilityGate:
    """A per-call gate unless the caller supplies one.

    This used to be a module-level singleton whose `pending` dict grew an entry
    for every classified command and was never popped, so a long session
    accumulated every rejected command string. Nothing can resume from a pending
    entry today, so it dies with the call. A persistent queue is the right
    answer only once a resumable caller exists — forge-play's `human_loop` is
    the design to adopt then.
    """
    return gate if gate is not None else CapabilityGate()


def dispatch(
    name: str,
    inputs: dict,
    mcp_names: set,
    mcp_call,
    *,
    trusted: bool = False,
    policy_store: PolicyStore | None = None,
    hook_runtime: HookRuntime | None = None,
    gate: CapabilityGate | None = None,
) -> object:
    tool_use_id = str(uuid.uuid4())
    if hook_runtime is not None:
        hook_runtime.run_event(
            "PreTool",
            {"tool_name": name, "tool_input": inputs, "tool_use_id": tool_use_id},
        )

    # The single chokepoint. Every verdict is resolved here, before any tool
    # body runs, so a branch below cannot be reached around.
    decision = check(name, inputs, trusted=trusted, policy=policy_store, gate=_gate_for(gate))
    if decision.verdict is Verdict.DENY:
        if hook_runtime is not None:
            hook_runtime.run_event(
                "PermissionDenied",
                {"tool_name": name, "tool_input": inputs, "tool_use_id": tool_use_id},
            )
        return json.dumps({"error": decision.reason})
    if decision.verdict is Verdict.CONFIRM:
        raise NeedsConfirmation(name, inputs, decision)
    trusted = True

    if name == "Bash":
        cmd = inputs.get("command", "")
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
                env=child_env(),
            )
            output = redact((result.stdout + result.stderr).strip()) or "(no output)"
            if hook_runtime is not None:
                hook_runtime.run_event(
                    "PostTool",
                    {"tool_name": name, "tool_input": inputs, "tool_output": output, "tool_use_id": tool_use_id},
                )
            return output
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out ({_BASH_TIMEOUT}s)"
        except Exception as exc:
            return f"ERROR: {exc}"

    if name == "Read":
        path = inputs.get("file_path", "") or inputs.get("path", "")
        try:
            output = redact(Path(path).read_text(encoding="utf-8", errors="replace")[:_MAX_READ])
            if hook_runtime is not None:
                hook_runtime.run_event(
                    "PostTool",
                    {"tool_name": name, "tool_input": inputs, "tool_output": output, "tool_use_id": tool_use_id},
                )
            return output
        except Exception as exc:
            return f"ERROR: {exc}"

    if name == "Write":
        path = inputs.get("file_path", "")
        content = inputs.get("content", "")
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            output = f"Written {len(content)} chars to {path}"
            if hook_runtime is not None:
                hook_runtime.run_event(
                    "PostTool",
                    {"tool_name": name, "tool_input": inputs, "tool_output": output, "tool_use_id": tool_use_id},
                )
            return output
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
            output = f"Edited {path}"
            if hook_runtime is not None:
                hook_runtime.run_event(
                    "PostTool",
                    {"tool_name": name, "tool_input": inputs, "tool_output": output, "tool_use_id": tool_use_id},
                )
            return output
        except Exception as exc:
            return f"ERROR: {exc}"

    if name == "Glob":
        import glob as _glob

        pattern = inputs.get("pattern", "")
        cwd = inputs.get("cwd", "") or str(Path.cwd())
        try:
            matches = sorted(_glob.glob(pattern, root_dir=cwd, recursive=True))
            output = "\n".join(matches) if matches else "(no matches)"
            if hook_runtime is not None:
                hook_runtime.run_event(
                    "PostTool",
                    {"tool_name": name, "tool_input": inputs, "tool_output": output, "tool_use_id": tool_use_id},
                )
            return output
        except Exception as exc:
            return f"ERROR: {exc}"

    if name in mcp_names and mcp_call is not None:
        output = mcp_call(name, inputs)
        if hook_runtime is not None:
            hook_runtime.run_event(
                "PostTool",
                {"tool_name": name, "tool_input": inputs, "tool_output": str(output), "tool_use_id": tool_use_id},
            )
        return output

    return f"[stub] tool '{name}' not wired. inputs={json.dumps(inputs)[:120]}"


def prompt_and_dispatch(
    name: str,
    inputs: dict,
    trusted: bool,
    mcp_names: set,
    mcp_call,
    policy_store: PolicyStore | None = None,
    hook_runtime: HookRuntime | None = None,
) -> object:
    """Resolve a CONFIRM by asking the human. The CLI's answer to the seam.

    It no longer re-derives the verdict from `trusted` — that is what made an
    explicit `Write -> confirm` rule a no-op under `--trust`. It runs the call,
    and only if `dispatch` says a human is needed does it ask.
    """
    gate = CapabilityGate()
    try:
        return dispatch(
            name,
            inputs,
            mcp_names,
            mcp_call,
            trusted=trusted,
            policy_store=policy_store,
            hook_runtime=hook_runtime,
            gate=gate,
        )
    except NeedsConfirmation as needs:
        summary = json.dumps(inputs, ensure_ascii=False)[:200]
        print(f"\n  [tool:{name}] {summary}")
        print(f"  reason: {needs.decision.reason}")
        try:
            answer = input("  Allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "y":
            return json.dumps({"error": "user denied"})
        # One-shot approval for this call only: re-enter with the verdict
        # already resolved, never by widening the policy.
        return dispatch(
            name,
            inputs,
            mcp_names,
            mcp_call,
            trusted=True,
            policy_store=None,
            hook_runtime=hook_runtime,
            gate=gate,
        )
