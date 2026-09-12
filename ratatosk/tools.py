"""Tool definitions and dispatch for the Ratatosk tool loop."""

from __future__ import annotations

import json
import shlex
import subprocess
import uuid
from pathlib import Path

from ratatosk.capabilities import CapabilityGate
from ratatosk.child_env import child_env
from ratatosk.hooks import HookRuntime, merged_input
from ratatosk.hooks import blocking as hooks_blocking
from ratatosk.permission import NeedsConfirmation, Verdict, check
from ratatosk.policy import PolicyStore
from ratatosk.redact import redact

BASH_TOOL = {
    "name": "Bash",
    "description": (
        "Run a single program directly. There is no shell: the command is split "
        "into POSIX words and executed with shell=False, so pipes, redirects, "
        "globs, &&/||/;, $VAR expansion, backticks and subshells do NOT work — "
        "they are passed through as literal arguments. Run one program with its "
        "arguments. High-risk: requires confirmation unless --trust."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "A single program and its arguments, e.g. 'ls -la /tmp'. "
                    "Not a shell line."
                ),
            },
        },
        "required": ["command"],
    },
}

READ_TOOL = {
    "name": "Read",
    "description": (
        "Read a file and return its contents with line numbers. Long files are "
        "truncated with an explicit marker naming how much was omitted."
    ),
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

#: Programs that are a shell in disguise. The Bash tool promises "no shell";
#: `sh -c` would quietly make that false, so it is refused rather than being
#: left as a hole the description does not mention.
_SHELL_IN_DISGUISE: dict[str, tuple[str, ...]] = {
    "sh": ("-c",),
    "bash": ("-c",),
    "zsh": ("-c",),
    "dash": ("-c",),
    "ksh": ("-c",),
    "python": ("-c",),
    "python3": ("-c",),
    "perl": ("-e",),
    "ruby": ("-e",),
    "node": ("-e", "--eval"),
}


def problem(fault: str, remedy: str = "") -> str:
    """The one shape a failed tool call comes back in.

    `dispatch` used to return three: a JSON `{"error": ...}` for a policy
    denial, a bare `ERROR: {exc}` for a tool failure, and `[stub] tool 'x' not
    wired` for an unknown tool. The model could not reliably tell "I called this
    wrongly" from "the tool broke" from "that tool does not exist" — three
    situations with three different next moves.

    Prose, not a machine-readable `kind`. The reader is a language model, and a
    rigid structure costs accuracy by making it parse a schema while it reasons.
    The target already existed in this file: "old_string matches 3 times — must
    be unique" says the fault and the remedy in one line.
    """
    return f"{fault}{chr(10) + remedy if remedy else ''}"


def _clip(text: str, limit: int, unit: str = "characters") -> str:
    """Truncate loudly. Silence is indistinguishable from a short file."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    omitted = len(text) - limit
    return (
        f"{head}\n\n[truncated: showing the first {limit:,} of {len(text):,} {unit}; "
        f"{omitted:,} omitted. Read a specific range to see more.]"
    )


def _numbered(text: str) -> str:
    lines = text.splitlines()
    width = len(str(len(lines))) if lines else 1
    return "\n".join(f"{i:>{width}}\t{line}" for i, line in enumerate(lines, 1))


def _reject_shell_in_disguise(argv: list[str]) -> str | None:
    program = Path(argv[0]).name
    flags = _SHELL_IN_DISGUISE.get(program)
    if not flags:
        return None
    if any(arg in flags for arg in argv[1:]):
        return problem(
            f"Refused: `{program} {flags[0]}` runs a shell, and this tool does not provide one.",
            "Run the program directly with its arguments, or write a script file and run that.",
        )
    return None


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


def _run_tool(name: str, inputs: dict, mcp_names: set, mcp_call) -> object:
    """Run one tool body. Raises on failure; the caller owns the error shape.

    Extracted so PostTool has exactly one emission point. It used to be emitted
    inside each of seven success branches, which is why every `except` returned
    without firing it — the stream had holes precisely where a hook would want
    them.
    """
    if name == "Bash":
        cmd = inputs.get("command", "")
        try:
            argv = shlex.split(cmd, posix=True)
        except ValueError as exc:
            return problem(
                f"Could not parse the command: {exc}.",
                "Check for an unbalanced quote. This tool splits into POSIX words, not a shell line.",
            )
        if not argv:
            return problem(
                "The command was empty.", "Pass a program name and its arguments."
            )
        refusal = _reject_shell_in_disguise(argv)
        if refusal:
            return refusal
        return _run_bash(argv)

    if name == "Read":
        path = inputs.get("file_path", "") or inputs.get("path", "")
        if not path:
            return problem(
                "No file_path was given.", "Pass an absolute path in file_path."
            )
        target = Path(path)
        if not target.exists():
            return problem(
                f"{path} does not exist.",
                "Check the path, or use Glob to find it.",
            )
        if target.is_dir():
            return problem(
                f"{path} is a directory, not a file.",
                "Use Glob to list its contents.",
            )
        return _clip(
            redact(_numbered(target.read_text(encoding="utf-8", errors="replace"))),
            _MAX_READ,
        )

    if name == "Write":
        path = inputs.get("file_path", "")
        content = inputs.get("content", "")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {path}"

    if name == "Edit":
        path = inputs.get("file_path", "")
        old = inputs.get("old_string", "")
        new = inputs.get("new_string", "")
        text = Path(path).read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            return problem(
                f"old_string was not found in {path}.",
                "Read the file first and copy the text exactly, including whitespace.",
            )
        if count > 1:
            return problem(
                f"old_string matches {count} times in {path} — must be unique.",
                "Include more surrounding lines so the match is unambiguous.",
            )
        Path(path).write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path}"

    if name == "Glob":
        import glob as _glob

        pattern = inputs.get("pattern", "")
        cwd = inputs.get("cwd", "") or str(Path.cwd())
        matches = sorted(_glob.glob(pattern, root_dir=cwd, recursive=True))
        return "\n".join(matches) if matches else "(no matches)"

    if name in mcp_names and mcp_call is not None:
        return mcp_call(name, inputs)

    return problem(
        f"There is no tool called '{name}'.",
        "Use one of the tools listed for this session; check the spelling.",
    )


def _run_bash(argv: list[str]) -> str:
    """Run one program, and kill its whole process group on timeout.

    `subprocess.run(timeout=)` signals only the direct child, so a program that
    spawned its own children left them running after the timeout fired.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env(),
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=_BASH_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        return problem(
            f"The command timed out after {_BASH_TIMEOUT}s and was killed.",
            "Run something shorter, or redirect long output to a file and read that.",
        )
    combined = redact((out + err).strip())
    if not combined:
        return "(no output)"
    return _clip(combined, _MAX_READ)


def _kill_group(proc: subprocess.Popen) -> None:
    import os
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.terminate()
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()


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
    output: object = None
    fired = False

    def fire_post(value: object) -> None:
        nonlocal fired
        if hook_runtime is not None and not fired:
            fired = True
            hook_runtime.run_event(
                "PostTool",
                {
                    "tool_name": name,
                    "tool_input": inputs,
                    "tool_output": str(value),
                    "tool_use_id": tool_use_id,
                },
            )

    if hook_runtime is not None:
        pre = hook_runtime.run_event(
            "PreTool",
            {"tool_name": name, "tool_input": inputs, "tool_use_id": tool_use_id},
        )
        # A PreTool hook can now stop the call. Composed with the permission
        # check below as: deny wins from either side. A hook may be the more
        # restrictive voice, never the less.
        block = hooks_blocking(pre)
        if block is not None:
            hook_runtime.run_event(
                "PermissionDenied",
                {"tool_name": name, "tool_input": inputs, "tool_use_id": tool_use_id},
            )
            return problem(
                f"{name} was blocked by the hook {block.script}: {block.reason}",
                "Adjust the call, or change that hook in hooks.json.",
            )
        # A hook may adjust an argument it can already see. Re-validated below,
        # so a mutation cannot buy permission it did not have.
        inputs = merged_input(pre, inputs)

    # The single chokepoint. Every verdict is resolved here, before any tool
    # body runs, so a branch below cannot be reached around.
    decision = check(
        name, inputs, trusted=trusted, policy=policy_store, gate=_gate_for(gate)
    )
    if decision.verdict is Verdict.DENY:
        if hook_runtime is not None:
            hook_runtime.run_event(
                "PermissionDenied",
                {"tool_name": name, "tool_input": inputs, "tool_use_id": tool_use_id},
            )
        return problem(
            f"{name} was refused: {decision.reason}.",
            "Ask the operator to allow it with /permissions, or use a different tool.",
        )
    if decision.verdict is Verdict.CONFIRM:
        raise NeedsConfirmation(name, inputs, decision)

    try:
        output = _run_tool(name, inputs, mcp_names, mcp_call)
    except Exception as exc:
        output = problem(f"{name} failed: {exc}.", "Check the arguments and try again.")
        return output
    finally:
        # Unconditional: the stream must not have holes where a tool failed.
        fire_post(output)
    return output


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
            return problem(
                f"The operator declined to run {name}.",
                "Do not retry the same call; ask what they would prefer.",
            )
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
