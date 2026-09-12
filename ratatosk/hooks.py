"""Lifecycle and tool hook runtime.

Three defects fixed together because they are one defect wearing three hats:
the runtime did not take hooks seriously as programs.

- Every hook was launched as ``[sys.executable, script]``, so a shell hook was
  handed to Python and failed with a SyntaxError nobody read.
- ``PreTool`` collected exit codes into ``HookResult`` and `dispatch` ignored
  them, so the pre-tool event was advisory — not what the name implies to
  anyone configuring one.
- ``PostTool`` fired only on success paths, so the stream had holes exactly
  where a hook would want them.

The exit-code contract is the load-bearing decision. **2 is the only deliberate
block**; any other nonzero is a crash. Conflating them means a hook with a typo
silently denies every tool call, which is the failure mode that teaches people
to stop using hooks.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ratatosk.child_env import child_env
from ratatosk.paths import ratatosk_data_root

#: Exit code a hook uses to say "do not run this tool". Anything else nonzero
#: is treated as the hook itself failing.
BLOCK_EXIT_CODE = 2

#: Events where a broken hook should stop the call rather than wave it through.
_DEFAULT_DENY_EVENTS = frozenset({"PreTool"})

_EXTENSION_INTERPRETERS: dict[str, list[str]] = {
    ".py": [sys.executable],
    ".sh": ["/bin/sh"],
    ".bash": ["/bin/bash"],
}


@dataclass
class HookResult:
    script: str
    ok: bool
    output: str
    #: True when the hook deliberately blocked (exit 2), or when it failed and
    #: this event's on_failure policy is "deny".
    blocked: bool = False
    #: "allow" | "block" | "error"
    decision: str = "allow"
    reason: str = ""
    #: A shallow merge the hook asked for over the tool's declared inputs.
    updated_input: dict = field(default_factory=dict)


def _is_world_writable(path: Path) -> bool:
    """World-writable only — deliberately not group-writable.

    A group-writable file is normal under umask 002, which is the default on
    Debian/Ubuntu and on this fleet's own nodes, where each user has a private
    group. Refusing those would refuse almost every hook anyone writes, and a
    security check that fires constantly on correct configuration is one people
    route around. World-writable is the case that is wrong on every system.
    """
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & stat.S_IWOTH)


def _launch_argv(script: Path, spec: dict) -> list[str] | None:
    """How to run this hook. None means "refuse, loudly".

    Order: an explicit `interpreter` wins; then an executable file runs itself
    so its shebang is honoured; then a known extension; then nothing. Never
    `shell=True` — a hook path is configuration, but its arguments would not be.
    """
    interpreter = spec.get("interpreter")
    if interpreter:
        if isinstance(interpreter, str):
            return [interpreter, str(script)]
        if isinstance(interpreter, list) and all(
            isinstance(p, str) for p in interpreter
        ):
            return [*interpreter, str(script)]
        return None
    if os.name == "posix" and os.access(script, os.X_OK):
        return [str(script)]
    argv = _EXTENSION_INTERPRETERS.get(script.suffix.lower())
    if argv:
        return [*argv, str(script)]
    return None


def _parse_hook_stdout(raw: bytes) -> dict:
    """A hook may speak JSON on stdout. It is not required to."""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace").strip() or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class HookRuntime:
    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or (ratatosk_data_root() / "hooks.json")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            return {"events": {}}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {"events": {}}

    def list_events(self) -> dict[str, list[dict]]:
        data = self._load_config()
        events = data.get("events", {})
        return events if isinstance(events, dict) else {}

    def run_event(
        self, event_name: str, payload: dict | None = None
    ) -> list[HookResult]:
        on_failure_default = "deny" if event_name in _DEFAULT_DENY_EVENTS else "allow"

        if _is_world_writable(self.config_path):
            # A writable hook config is a way to run code as this user. Refuse
            # the whole event and say why, rather than executing from it.
            why = f"{self.config_path} is world-writable"
            return [
                HookResult(
                    script=str(self.config_path),
                    ok=False,
                    output=why,
                    blocked=on_failure_default == "deny",
                    decision="error",
                    reason=why,
                )
            ]

        specs = self.list_events().get(event_name, [])
        results: list[HookResult] = []
        for spec in specs:
            if isinstance(spec, dict):
                results.append(self._run_one(spec, payload, on_failure_default))
        return results

    def _run_one(
        self, spec: dict, payload: dict | None, on_failure_default: str
    ) -> HookResult:
        script_str = str(spec.get("script", "")).strip()
        on_failure = str(spec.get("on_failure", on_failure_default)).lower()
        if on_failure not in {"deny", "allow"}:
            on_failure = on_failure_default
        deny_on_failure = on_failure == "deny"

        def failed(output: str) -> HookResult:
            return HookResult(
                script=script_str,
                ok=False,
                output=output,
                blocked=deny_on_failure,
                decision="error",
                reason=output,
            )

        if not script_str:
            return failed("hook has no script")
        script = Path(script_str)
        if not script.exists():
            return failed(f"hook script does not exist: {script}")
        if _is_world_writable(script):
            return failed(f"hook script is world-writable: {script}")

        argv = _launch_argv(script, spec)
        if argv is None:
            # The old runtime handed this to Python and let it fail silently.
            return failed(
                f"cannot run {script}: not executable and no known interpreter for "
                f"'{script.suffix}'. Mark it executable, or set \"interpreter\" in hooks.json."
            )

        timeout_s = int(spec.get("timeout_s", 10))
        stdin = json.dumps(payload or {}).encode("utf-8")
        try:
            proc = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                timeout=timeout_s,
                check=False,
                env=child_env(),
            )
        except subprocess.TimeoutExpired:
            # A timeout is a crash, not a considered refusal.
            return failed(f"hook timed out after {timeout_s}s: {script}")
        except Exception as exc:
            return failed(f"hook failed to start: {exc}")

        out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").strip()
        parsed = _parse_hook_stdout(proc.stdout)

        if proc.returncode == 0:
            decision = str(parsed.get("decision", "allow")).lower()
            blocked = decision == "block"
            return HookResult(
                script=script_str,
                ok=True,
                output=out,
                blocked=blocked,
                decision="block" if blocked else "allow",
                reason=str(parsed.get("reason", ""))
                or ("blocked by hook" if blocked else ""),
                updated_input=parsed.get("updated_input") or {},
            )

        if proc.returncode == BLOCK_EXIT_CODE:
            return HookResult(
                script=script_str,
                ok=True,
                output=out,
                blocked=True,
                decision="block",
                reason=str(parsed.get("reason", "")) or out or f"blocked by {script}",
            )

        return failed(out or f"hook exited {proc.returncode}: {script}")


def blocking(results: list[HookResult]) -> HookResult | None:
    """The first result that says stop, if any."""
    for result in results:
        if result.blocked:
            return result
    return None


def merged_input(results: list[HookResult], inputs: dict) -> dict:
    """Apply hooks' `updated_input` over the tool's inputs.

    Shallow, and only over keys the caller already declared — a hook may adjust
    an argument, not invent one. The result is re-validated by
    `permission.check` afterwards, so a mutation cannot buy permission.
    """
    out = dict(inputs)
    for result in results:
        for key, value in (result.updated_input or {}).items():
            if key in inputs:
                out[key] = value
    return out
