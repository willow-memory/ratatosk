"""Ratatosk entry point — platform session runtime."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ratatosk import grove as _grove
from ratatosk import seat as _seat
from ratatosk import session as _session
from ratatosk import sync as _sync
from ratatosk import tools as _tools
from ratatosk.capabilities import CapabilityGate
from ratatosk.history import (
    ensure_history_db,
    index_session,
    list_sessions,
    load_session_history,
    search_sessions,
)
from ratatosk.hooks import HookRuntime
from ratatosk.inference import InferenceRouter, LadderRefused
from ratatosk.ladder import LadderError
from ratatosk.permission import check as _permission_check
from ratatosk.policy import PolicyStore, shadowed_rules, subject_field
from ratatosk.redact import redact

_HOME = Path.home()
_MAX_TURNS = 20
_MAX_CHARS = 200_000


def _resolve_willow_root() -> Path:
    """Where fleet-wide context (CLAUDE.md) lives.

    The old default was a hardcoded ``~/github/willow-memory/willow`` — a
    machine-specific path that does not exist on the reference node, so the
    fallback silently resolved to nothing. Prefer the explicit variable, then
    derive from WILLOW_HOME (the fleet already routes data through it, see
    paths.ratatosk_data_root), and only then guess.
    """
    explicit = os.environ.get("WILLOW_ROOT")
    if explicit:
        return Path(explicit)
    for key in ("WILLOW_HOME", "WILLOW_STORE_ROOT"):
        value = os.environ.get(key)
        if value:
            return Path(value).parent
    return _HOME / "github" / "willow-memory"


_WILLOW_ROOT = _resolve_willow_root()


def _blocks(message: dict) -> list[dict]:
    content = message.get("content")
    return (
        [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    )


def _tool_use_ids(message: dict) -> set[str]:
    return {
        b.get("id")
        for b in _blocks(message)
        if b.get("type") == "tool_use" and b.get("id")
    }


def _tool_result_ids(message: dict) -> set[str]:
    return {
        b.get("tool_use_id")
        for b in _blocks(message)
        if b.get("type") == "tool_result" and b.get("tool_use_id")
    }


def _orphans(history: list[dict]) -> set[str]:
    """tool_result ids in `history` with no matching tool_use before them."""
    seen: set[str] = set()
    orphaned: set[str] = set()
    for message in history:
        orphaned |= _tool_result_ids(message) - seen
        seen |= _tool_use_ids(message)
    return orphaned


def _safe_start(history: list[dict], index: int) -> int:
    """Walk back until the slice starting at `index` has no orphaned results.

    The API rejects a `tool_result` whose `tool_use` is not in the conversation,
    so a blind tail slice fails the first time compaction triggers in a
    tool-heavy session: the leading edge lands on a user message of results
    whose assistant turn was just dropped.
    """
    while index > 0 and _orphans(history[index:]):
        index -= 1
    return index


def _overhead(state: RuntimeState | None) -> int:
    """What the budget was ignoring: the system prompt and the tool schemas.

    `_MAX_CHARS` counted the history alone. With MCP connected the serialized
    tool schemas are plausibly the largest single contributor to a request, so
    the budget was measuring the smaller half and calling it the total.
    """
    if state is None:
        return 0
    try:
        return len(state.system_prompt) + len(json.dumps(state.all_tools))
    except Exception:
        return len(state.system_prompt or "")


def _size(history: list[dict]) -> int:
    return sum(
        len(m["content"])
        if isinstance(m["content"], str)
        else sum(len(str(b)) for b in m["content"])
        for m in history
    )


@dataclass(frozen=True)
class CompactionReceipt:
    """What a compaction actually dropped.

    Compaction used to report itself as "keeping last 20 turns" whatever it
    did — but the loop below drops by *budget*, so it routinely keeps fewer,
    and with MCP connected the tool schemas can eat most of the budget before
    any history is counted. The number in the message was a constant, not a
    measurement.

    Nothing reached the transcript either. The note went into the model's
    history only, so the JSONL — what `/resume` reads and what the tier-0
    deposit carries — had no record that anything was forgotten. A session
    resumed after compaction looked like a session that had simply been short.
    """

    dropped: int
    kept: int
    chars_before: int
    chars_after: int
    #: "turn cap" or "budget" — which limit actually bit.
    reason: str

    def describe(self) -> str:
        return (
            f"compacted {self.dropped} message(s), kept {self.kept} "
            f"({self.chars_before:,}→{self.chars_after:,} chars, {self.reason})"
        )


def _compact(
    history: list[dict], state: RuntimeState | None = None
) -> tuple[list[dict], CompactionReceipt | None]:
    """Trim history to fit, and say what that cost.

    Returns the receipt rather than a bare flag; `None` means nothing was
    dropped. It stays truthy-on-compaction so existing `if compacted:` callers
    read the same.
    """
    budget = _MAX_CHARS - _overhead(state)
    size_before = _size(history)
    over_turns = len(history) > _MAX_TURNS * 2
    if size_before <= budget and not over_turns:
        return history, None

    start = _safe_start(history, max(0, len(history) - _MAX_TURNS * 2))

    # The turn cap is not the only limit. A short history of very large messages
    # can exceed the budget while sitting well inside _MAX_TURNS, and with MCP
    # connected the tool schemas can eat most of the budget before any history
    # is counted at all. Keep dropping from the front, on safe boundaries, until
    # it fits — or until only the last exchange is left, which is the floor.
    while start < len(history) - 2 and _size(history[start:]) > budget:
        start = _safe_start(history, start + 1)
        if start >= len(history) - 2:
            break

    keep = history[start:]

    # Belt and braces. The scan should make this impossible, but a dangling
    # tool_result can also arrive from a *construction* bug with no truncation
    # involved, and sending one costs a turn and an API error. Dropping the
    # offending results is worse than keeping more history, so keep more.
    if _orphans(keep):
        keep = history

    dropped = len(history) - len(keep)
    if dropped == 0:
        return history, None
    notice = {
        "role": "user",
        "content": f"[System note: compacted {dropped} earlier messages. Continue from current context.]",
    }
    kept = [notice] + keep
    receipt = CompactionReceipt(
        dropped=dropped,
        kept=len(keep),
        chars_before=size_before,
        chars_after=_size(kept),
        # Which limit bit: over the turn cap even at full size, or over budget.
        reason="turn cap" if over_turns and size_before <= budget else "budget",
    )
    return kept, receipt


def _record_compaction(state: RuntimeState, receipt: CompactionReceipt) -> None:
    """Tell the operator and the transcript what was forgotten.

    The transcript half is the point. `/resume` reads the JSONL and the tier-0
    deposit carries it, so a compaction that leaves no mark there is a gap the
    record cannot show — the session simply looks shorter than it was.
    """
    print(f"  [compacted] {receipt.describe()}", flush=True)
    try:
        state.writer.write_system(f"[compacted] {receipt.describe()}")
    except Exception as exc:  # a receipt must not be able to end the session
        print(f"  [compacted] receipt not written: {exc}", flush=True)


def _load_system_prompt() -> str:
    paths = [
        _WILLOW_ROOT / "CLAUDE.md",
        _HOME / "CLAUDE.md",
    ]
    parts = []
    for path in paths:
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"# {path}\n\n{content}")
    return (
        "\n\n---\n\n".join(parts)
        if parts
        else "You are Ratatosk — platform session runtime."
    )


def _credential_files() -> list[Path]:
    return [_HOME / ".ratatosk" / "credentials.json", _WILLOW_ROOT / "credentials.json"]


def _load_api_key() -> str:
    """Return the key. The environment is a *source*, never a destination.

    This used to write a file-loaded key back into ``os.environ``, which handed
    it to every subprocess ratatosk spawns. The value now goes straight to its
    one call site — `anthropic.Anthropic(api_key=...)` — the way willow-mcp's
    `integrations.credential()` does it.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    for candidate in _credential_files():
        if candidate.exists():
            # A credentials file that will not parse is a file to skip, not a
            # reason to refuse the ones after it. Deliberately silent: the
            # failure to read a key must never be reported beside the key.
            with contextlib.suppress(Exception):
                data = json.loads(candidate.read_text(encoding="utf-8"))
                key = data.get("ANTHROPIC_API_KEY", "")
                if key:
                    return key
    return ""


def credential_source() -> str:
    """Where the key comes from — never the key itself.

    `/doctor` used to answer this by reading `os.environ`, which only worked
    because `_load_api_key` had written to it. It reports provenance now, which
    is more useful anyway: "set" never distinguished a key from the shell from
    one out of a credentials file.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "env:ANTHROPIC_API_KEY"
    for candidate in _credential_files():
        if candidate.exists():
            with contextlib.suppress(Exception):  # same rule as _load_api_key
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("ANTHROPIC_API_KEY"):
                    return f"file:{candidate}"
    return "missing"


def _render_transcript(entries: list[dict]) -> str:
    lines: list[str] = []
    for entry in entries:
        role = entry.get("message", {}).get("role", entry.get("type", "unknown"))
        content = entry.get("message", {}).get("content", "")
        if isinstance(content, list):
            content = str(content)
        lines.append(f"{role}: {content}")
    return redact("\n\n".join(lines))


@dataclass
class RuntimeState:
    args: argparse.Namespace
    model: str
    writer: _session.SessionWriter
    history: list[dict]
    system_prompt: str
    all_tools: list[dict]
    mcp_names: set[str]
    mcp_call: object
    client: object | None
    policy: PolicyStore
    hooks: HookRuntime
    resumed_from: str | None = None
    #: The ladder walker (ratatosk.inference). ``None`` only in tests that
    #: exercise the router without a model; ``_run_turn`` refuses without it.
    inference: object | None = None
    task_class: str = "chat"
    #: The seat this crown entered as (``ratatosk.seat.enter``), or ``None``
    #: when run without ``--app-id`` — the plain REPL, no persona, no handoff.
    seat: _seat.SeatEntry | None = None


class CommandRouter:
    def __init__(self, state: RuntimeState):
        self.state = state

    def run(self, user_input: str) -> bool:
        cmd, _, rest = user_input.partition(" ")
        arg = rest.strip()
        handler = {
            "/exit": self._cmd_exit,
            "/clear": self._cmd_clear,
            "/status": self._cmd_status,
            "/sessions": self._cmd_sessions,
            "/resume": self._cmd_resume,
            "/doctor": self._cmd_doctor,
            "/export": self._cmd_export,
            "/permissions": self._cmd_permissions,
            "/hooks": self._cmd_hooks,
            "/compact": self._cmd_compact,
            "/help": self._cmd_help,
        }.get(cmd)
        if handler is None:
            print(f"  unknown command: {cmd}")
            return False
        return handler(arg)

    def _cmd_exit(self, _arg: str) -> bool:
        return True

    def _cmd_clear(self, _arg: str) -> bool:
        self.state.history = []
        print("  history cleared")
        return False

    def _cmd_status(self, _arg: str) -> bool:
        receipt = _grove.last_receipt()
        print(f"  session    : {self.state.writer.session_id}")
        seat = self.state.seat
        if seat is not None:
            print(f"  seat       : {seat.app_id} ({seat.entry_mode})")
            print(f"  dispatch   : {seat.dispatch_id or '-'}")
            print(f"  closeout   : {seat.closeout_tool}")
        print(f"  turns      : {len(self.state.history) // 2}")
        print(f"  jsonl      : {self.state.writer.path}")
        print(f"  model      : {self.state.model}")
        if self.state.inference is not None:
            print(f"  rung       : {self.state.inference.describe_current()}")
        print(f"  mcp tools  : {len(self.state.mcp_names)}")
        print(f"  policy     : {self.state.policy.path}")
        print(f"  hooks cfg  : {self.state.hooks.config_path}")
        if receipt:
            print(f"  grove      : {receipt.detail}")
        return False

    def _cmd_sessions(self, arg: str) -> bool:
        limit = 10
        if arg.isdigit():
            limit = max(1, min(100, int(arg)))
        rows = list_sessions(limit=limit)
        if not rows:
            print("  no indexed sessions yet")
            return False
        for row in rows:
            summary = row.first_prompt or "(empty prompt)"
            print(
                f"  {row.session_id[:8]}  {row.ended_at}  turns={row.turns}  {summary}"
            )
        return False

    def _cmd_resume(self, arg: str) -> bool:
        query = arg.strip()
        if not query:
            print("  usage: /resume <session_id_prefix|search_text>")
            return False
        matches = search_sessions(query, limit=10)
        exact = [m for m in matches if m.session_id.startswith(query)]
        chosen = exact[0] if exact else (matches[0] if matches else None)
        if chosen is None:
            print(f"  no session found for: {query}")
            return False
        prior = load_session_history(chosen.session_id)
        if not prior:
            print(f"  session found but history unavailable: {chosen.session_id}")
            return False
        note = f"[System note: resumed context from session {chosen.session_id[:8]}]"
        self.state.history = [{"role": "user", "content": note}] + prior[
            -(_MAX_TURNS * 2) :
        ]
        self.state.resumed_from = chosen.session_id
        self.state.writer.write_system(note)
        print(f"  resumed from {chosen.session_id} ({len(prior)} message(s) loaded)")
        return False

    def _cmd_doctor(self, _arg: str) -> bool:
        from ratatosk import ollama

        checks = [
            ("python", sys.version.split()[0]),
            ("session_dir", str(self.state.writer.path.parent)),
            ("history_db", str(ensure_history_db())),
            ("policy_file", str(self.state.policy.path)),
            ("hooks_file", str(self.state.hooks.config_path)),
            ("api_key", credential_source()),
            ("ollama", "up" if ollama.is_available() else "down"),
            ("mcp", "connected" if self.state.mcp_call is not None else "disabled"),
        ]
        for key, value in checks:
            print(f"  {key:12}: {value}")
        # The ladder, rung by rung: usable | no-key | stale-verify | refused,
        # each with its reason. The old `api_key` line above answered for one
        # provider; this answers for all of them without naming a value.
        inference = self.state.inference
        if inference is None:
            print("  ladder      : not loaded")
            return False
        print(f"  ladder      : {inference.ladder.path or '(in-memory)'}")
        for name, status, reason in inference.doctor_rows():
            print(f"    {name:12} {status:12} {reason}")
        return False

    def _cmd_export(self, arg: str) -> bool:
        target = arg or f"{self.state.writer.session_id}.transcript.txt"
        out = Path(target)
        if not out.is_absolute():
            out = Path.cwd() / out
        out.write_text(
            _render_transcript(self.state.writer.read_entries()), encoding="utf-8"
        )
        print(f"  exported: {out}")
        return False

    def _cmd_permissions(self, arg: str) -> bool:
        parts = arg.split()
        if not parts or parts[0] == "list":
            rules = self.state.policy.load()
            if not rules:
                print("  no rules")
                return False
            # A shadowed rule used to print exactly like a live one, so the
            # operator read a rule that could never fire and believed it.
            eaten = {s.index: s for s in shadowed_rules(rules)}
            for i, rule in enumerate(rules):
                line = f"  {rule.pattern:20} -> {rule.action}"
                hit = eaten.get(i)
                if hit is not None:
                    line += f"   [unreachable: '{hit.by.pattern}' above answers first]"
                print(line)
            if eaten:
                print(
                    f"  {len(eaten)} rule(s) never fire — remove them or reorder above the rule that eats them"
                )
            return False
        if parts[0] == "explain" and len(parts) >= 2:
            tool_name = parts[1]
            rest = arg.split(maxsplit=2)[2] if len(parts) > 2 else ""
            # Put the free text where a scoped rule would look for it, so
            # `explain Bash git status` and `explain Write /etc/passwd` both
            # ask the question the real call would ask.
            field = subject_field(tool_name)
            inputs = {field: rest} if field and rest else {}
            decision = _permission_check(
                tool_name,
                inputs,
                trusted=bool(getattr(self.state.args, "trust", False)),
                policy=self.state.policy,
                gate=CapabilityGate(),
            )
            print(f"  {tool_name} -> {decision.verdict.value}")
            print(f"  reason: {decision.reason or '(none given)'}")
            print(f"  source: {decision.source}")
            if field and not rest:
                print(
                    f"  note: no {field} given, so a rule scoped to one could not match"
                )
            print("  (dry run — nothing was executed)")
            return False
        # The pattern is everything between the verb and the action, not one
        # token: a scoped pattern contains spaces (`Bash(git status*)`) and
        # splitting on whitespace made it untypeable.
        if parts[0] == "set" and len(parts) >= 3:
            pattern, action = " ".join(parts[1:-1]), parts[-1]
            try:
                self.state.policy.set_rule(pattern, action)
            except ValueError as exc:
                print(f"  {exc}")
                return False
            print(f"  rule set: {pattern} -> {action}")
            return False
        if parts[0] == "remove" and len(parts) >= 2:
            pattern = " ".join(parts[1:])
            removed = self.state.policy.remove_rule(pattern)
            print("  removed" if removed else "  rule not found")
            return False
        print(
            "  usage: /permissions [list|explain <tool> [args]|"
            "set <pattern> <allow|deny|confirm>|remove <pattern>]"
        )
        return False

    def _cmd_hooks(self, arg: str) -> bool:
        parts = arg.split()
        if not parts or parts[0] == "list":
            events = self.state.hooks.list_events()
            if not events:
                print(f"  no hooks configured ({self.state.hooks.config_path})")
                return False
            for event_name, hooks in sorted(events.items()):
                print(f"  {event_name}: {len(hooks)}")
            return False
        if parts[0] == "run" and len(parts) >= 2:
            event_name = parts[1]
            results = self.state.hooks.run_event(event_name, {"source": "manual"})
            if not results:
                print(f"  no hooks for event: {event_name}")
                return False
            for result in results:
                status = "ok" if result.ok else "fail"
                print(f"  [{status}] {result.script} {result.output[:120]}")
            return False
        print("  usage: /hooks [list|run <EventName>]")
        return False

    def _cmd_compact(self, arg: str) -> bool:
        self.state.hooks.run_event("PreCompact", {"instructions": arg or None})
        before = len(self.state.history)
        self.state.history, compacted = _compact(self.state.history, self.state)
        if compacted:
            # "to last 20 turns" was a constant, not a measurement — the budget
            # loop routinely keeps fewer. Report what it did.
            message = f"[compacted] {compacted.describe()}"
            if arg:
                message += f" [{arg}]"
            self.state.writer.write_system(message)
            print(f"  {message}")
        else:
            print("  no compaction needed")
        self.state.hooks.run_event(
            "PostCompact", {"before": before, "after": len(self.state.history)}
        )
        return False

    def _cmd_help(self, _arg: str) -> bool:
        print(
            "  /exit /clear /status /sessions /resume /doctor /export /permissions /hooks /compact"
        )
        return False


def _run_turn(state: RuntimeState, user_input: str) -> None:
    state.writer.write_user(user_input)
    state.history.append({"role": "user", "content": user_input})

    inference = state.inference
    if inference is None:
        raise RuntimeError(
            "no inference router — crown.main builds one from the ladder"
        )

    # One loop for every dialect. The ladder walker returns Anthropic-shaped
    # blocks whichever rung answered, so the tool loop below does not know or
    # care whether groq, ollama or the SDK wrote them. The Anthropic client
    # streams to stdout itself (its `echo`); the others print when they land.
    while True:
        try:
            completion, receipt = inference.complete(
                state.system_prompt, state.history, state.all_tools
            )
        except LadderRefused as exc:
            # The receipt is already inked (JSONL + Grove). The user turn stays
            # in history so the operator can fix the rung and retry.
            print(f"\n  [ladder] {exc}", flush=True)
            state.writer.write_system(f"[ladder refused] {exc}")
            return
        if not _echoed(inference):
            print(completion.text, end="", flush=True)
        print()
        # The receipt, once, on the terminal too — the inked line, not a
        # paraphrase of it.
        print(f"  {receipt.line()}", flush=True)
        assistant_content = _non_empty(completion.blocks)
        if assistant_content is None:
            # A rung that answered nothing — no text, no tool call. An empty
            # assistant message is not a message: the Anthropic API rejects
            # an empty content list (400) on every later turn, so one blank
            # answer from a free rung would poison the paid one until /clear.
            # Nothing goes into history; the transcript records the blank.
            note = f"[empty answer] {receipt.rung} returned no content"
            print(f"  {note}", flush=True)
            state.writer.write_system(note)
            break
        state.writer.write_assistant(completion.text)
        state.history.append({"role": "assistant", "content": assistant_content})
        tool_uses = completion.tool_uses
        if not tool_uses:
            state.history, compacted = _compact(state.history, state)
            if compacted:
                _record_compaction(state, compacted)
            break

        tool_results = []
        for tu in tool_uses:
            result = _tools.prompt_and_dispatch(
                tu["name"],
                tu["input"],
                state.args.trust,
                state.mcp_names,
                state.mcp_call,
                policy_store=state.policy,
                hook_runtime=state.hooks,
            )
            print(f"  [tool:{tu['name']}] → {str(result)[:120]}", flush=True)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": tu["id"], "content": str(result)}
            )
        state.history.append({"role": "user", "content": tool_results})


def _non_empty(blocks: list[dict]) -> list[dict] | None:
    """The blocks worth keeping, or None when there is nothing to keep.

    A text block with empty text is dropped too — the Anthropic API refuses
    "text content blocks must be non-empty" the same way it refuses an
    empty list.
    """
    kept = [
        b for b in blocks if b.get("type") != "text" or str(b.get("text", "")).strip()
    ]
    return kept or None


def _echoed(inference) -> bool:
    """Did the rung that just answered already stream its text to stdout?

    Only the Anthropic client streams; printing its text again would show
    every answer twice.
    """
    current = getattr(inference, "current", None)
    rung = getattr(current, "rung", None)
    return getattr(rung, "dialect", None) == "anthropic"


def _shutdown(state: RuntimeState) -> None:
    """Close the session down. Runs from a `finally`, so it must not raise.

    Every step here was previously straight-line code after the REPL loop, which
    meant anything escaping the loop — a KeyboardInterrupt, most of all — skipped
    all of it: the JSONL survived (it is written per entry) but was never
    indexed, so `/sessions` and `/resume` could not see it and the tier-0 deposit
    never happened. Each step is guarded independently so one failure cannot
    strand the rest.
    """
    writer = state.writer
    turns = len(state.history) // 2

    # The seat's own closeout first — it speaks to willow-mcp, so it must run
    # while the transport is still up, and before the bus hears "session
    # ended". A failed handoff is printed and inked, never swallowed: the
    # JSONL still lands and is still indexed below either way.
    if state.seat is not None and state.mcp_call is not None:
        try:
            result = _seat.close(
                state.mcp_call, state.seat, writer.read_entries(), str(writer.path)
            )
        except Exception as exc:  # close() should not raise; belt and braces
            result = {"error": f"closeout raised: {exc}"}
        receipt = _seat.closed_receipt(state.seat, result)
        line = _seat.ink(writer, receipt)
        print(f"  {line}", flush=True)

    if state.args.mcp:
        try:
            from ratatosk import mcp_client

            if not mcp_client.shutdown():
                print(
                    "  [mcp] stdio teardown did not finish within timeout", flush=True
                )
        except Exception as exc:
            print(f"  [mcp] shutdown failed: {exc}", flush=True)

    try:
        end_receipt = _grove.session_ended(writer.session_id, turns, str(writer.path))
        if not end_receipt.skipped and not end_receipt.ok:
            print(f"  [grove] {end_receipt.detail}", flush=True)
    except Exception as exc:
        print(f"  [grove] session_ended failed: {exc}", flush=True)

    try:
        state.hooks.run_event(
            "SessionEnd", {"session_id": writer.session_id, "turns": turns}
        )
    except Exception as exc:
        print(f"  [hooks] SessionEnd failed: {exc}", flush=True)

    # `write` creates the JSONL on its first entry, so a session that ended
    # before writing anything leaves no file. Indexing it raised
    # FileNotFoundError — reported as "[history] index failed", as though
    # something had gone wrong — and then the closing line said
    # "Session written: <path>" for a path that does not exist. Two untruths
    # for one ordinary case: a session in which nothing happened.
    #
    # The answer is not to touch an empty file so the sentence comes true.
    # That would litter the session directory with rows about nothing.
    if not writer.path.exists():
        no_deposit = ", no deposit" if state.args.deposit else ""
        print(f"Session had no entries — nothing written, nothing indexed{no_deposit}.")
        return

    if state.args.deposit:
        try:
            print(f"  [deposit] {_sync.write_deposit(writer)}")
        except Exception as exc:
            print(f"  [deposit] failed: {exc}", flush=True)

    try:
        index_session(
            session_id=writer.session_id,
            cwd=writer.cwd,
            model=state.model,
            jsonl_path=writer.path,
            resumed_from=state.resumed_from,
        )
    except Exception as exc:
        print(f"  [history] index failed: {exc}", flush=True)

    print(f"Session written: {writer.path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ratatosk — Willow platform session runtime"
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Force one model onto the first usable rung of the class ladder. "
            "Unset: each rung's own model for the class."
        ),
    )
    parser.add_argument(
        "--class",
        dest="task_class",
        default="chat",
        choices=[
            "build",
            "audit",
            "research",
            "operate",
            "witness",
            "chat",
            "summarize",
            "classify",
        ],
        help="Task class — selects the provider ladder (provider_ladder.json)",
    )
    parser.add_argument(
        "--trust",
        action="store_true",
        help="Execute tools without per-call confirmation",
    )
    parser.add_argument(
        "--mcp", action="store_true", help="Connect to willow-mcp via stdio"
    )
    parser.add_argument("--local", action="store_true", help="Route to local Ollama")
    parser.add_argument(
        "--listen", action="store_true", help="Run Grove bus listener (requires --mcp)"
    )
    parser.add_argument(
        "--deposit", action="store_true", help="Write tier-0 session deposit on exit"
    )
    parser.add_argument(
        "--app-id",
        dest="app_id",
        default=None,
        help=(
            "Run as this fleet seat: session_enter through willow-mcp before the "
            "first turn, carry the persona the broker returns, hand off as the "
            "seat at close (requires --mcp)"
        ),
    )
    parser.add_argument(
        "--dispatch-id",
        dest="dispatch_id",
        default=None,
        help="Work this dispatch packet as the seat (requires --app-id)",
    )
    args = parser.parse_args()

    # The help text has always said --listen requires --mcp, but nothing
    # enforced it: the listener lives inside `if args.mcp`, so `--listen`
    # alone fell through to an ordinary REPL. Asking for a bus listener and
    # silently getting a chat prompt is the flag lying about what it did.
    if args.listen and not args.mcp:
        parser.error(
            "--listen requires --mcp: the bus listener speaks to willow-mcp over stdio"
        )
    # Same shape for the seat: the entry verb lives on the other end of the
    # stdio transport, so a seat without --mcp is a flag that cannot do what
    # it says.
    if args.app_id and not args.mcp:
        parser.error(
            "--app-id requires --mcp: session_enter speaks to willow-mcp over stdio"
        )
    if args.dispatch_id and not args.app_id:
        parser.error("--dispatch-id requires --app-id: a packet is worked by a seat")
    if args.app_id:
        try:
            _seat.check_app_id(args.app_id)
        except _seat.SeatRefused as exc:
            parser.error(str(exc))
        # The flag decides; the environment follows. The willow-mcp child we
        # spawn reads WILLOW_APP_ID (mcp_client forwards WILLOW_*), Grove sends
        # read RATATOSK_APP_ID, the listener's node reads WILLOW_AGENT_NAME —
        # all three must agree, and none may have been left over from a stale
        # shell picking a seat the operator did not name.
        os.environ["WILLOW_APP_ID"] = args.app_id
        os.environ["RATATOSK_APP_ID"] = args.app_id
        os.environ["WILLOW_AGENT_NAME"] = args.app_id

    # A configured channel and no transport is a misconfiguration the operator
    # should hear about at startup, not discover in a failed receipt halfway
    # through a session. Not an error: running without --mcp is legitimate, and
    # this refuses to guess that a channel means "start a server for me".
    if _grove.channel_env() and not args.mcp:
        print(
            f"  [grove] channel #{_grove.channel_env()} is set but --mcp was not passed — "
            "session events will not post",
            flush=True,
        )
        # Said once, here. Without this the start and end events each fail
        # separately and repeat it.
        _grove.disable("no MCP transport in this process")

    ensure_history_db()
    policy = PolicyStore()
    hooks = HookRuntime()

    mcp_names: set[str] = set()
    mcp_extra_tools: list[dict] = []
    mcp_call = None
    if args.mcp:
        print("  [mcp] connecting…", flush=True)
        from ratatosk import mcp_client

        mcp_extra_tools, mcp_names = mcp_client.start()
        mcp_call = mcp_client.call
        bound = _grove.connect(mcp_call)
        if not bound.ok:
            print(f"  [grove] {bound.detail}", flush=True)
        print(f"  [mcp] {len(mcp_names)} tools loaded", flush=True)
        if args.listen:
            # A listening seat is a seat: it enters once, so the broker has a
            # session for the node the bus will address, and its posts carry
            # the seat's name. Activation on WAKE is slice 3 — not wired here.
            if args.app_id:
                seat_id = f"{args.app_id}-listen-{os.getpid()}"
                try:
                    entered = _seat.enter(
                        mcp_call,
                        app_id=args.app_id,
                        session_id=seat_id,
                        project=os.environ.get("WILLOW_HANDOFF_PROJECT", ""),
                        workspace=str(Path.cwd()),
                    )
                except _seat.SeatRefused as exc:
                    print(f"  [seat] {exc}", flush=True)
                    if not mcp_client.shutdown():
                        print(
                            "  [mcp] stdio teardown did not finish within timeout",
                            flush=True,
                        )
                    raise SystemExit(2)
                print(f"  {_seat.ink(None, entered.receipt())}", flush=True)
            from ratatosk.listener import BusListener

            # No session writer on this path — there is no REPL and no
            # transcript — but the MCP server still needs a protocol teardown
            # rather than being killed by process exit. Ctrl-C is the normal
            # way this mode ends, so the finally is the only thing that runs.
            #
            # Construction is inside the try for the same reason: BusListener
            # refuses an unset channel, and that refusal arrives *after*
            # mcp_client.start() has spawned the server. Built above the try it
            # tracebacked out and left the child running.
            refused = ""
            try:
                listener = BusListener(mcp_call=mcp_call)
                listener.run_forever(
                    on_status=lambda msg: print(f"  [listen] {msg}", flush=True)
                )
            except KeyboardInterrupt:
                print("\n  [listen] stopped", flush=True)
            except ValueError as exc:
                refused = str(exc)
            finally:
                if not mcp_client.shutdown():
                    print(
                        "  [mcp] stdio teardown did not finish within timeout",
                        flush=True,
                    )
            if refused:
                # Same refusal the termux boot script prints, and the same exit
                # code: an unconfigured listener is a failure, not a quiet no-op.
                print(f"  [listen] {refused}", flush=True)
                raise SystemExit(1)
            return

    writer = _session.SessionWriter(cwd=str(Path.cwd()))

    # Enter as the seat before anything else is built: a refused entry (an
    # error result, or blockers on a specialist) means no loop, no ladder, no
    # prompt — exit 2 with the reason, the transport torn down. The session
    # id the broker records is the JSONL's, so the handoff and the transcript
    # name the same session.
    seat: _seat.SeatEntry | None = None
    if args.app_id:
        try:
            seat = _seat.enter(
                mcp_call,
                app_id=args.app_id,
                session_id=writer.session_id,
                dispatch_id=args.dispatch_id,
                project=os.environ.get("WILLOW_HANDOFF_PROJECT", ""),
                workspace=writer.cwd,
            )
        except _seat.SeatRefused as exc:
            print(f"ERROR: {exc}", flush=True)
            from ratatosk import mcp_client

            if not mcp_client.shutdown():
                print(
                    "  [mcp] stdio teardown did not finish within timeout", flush=True
                )
            sys.exit(2)
        print(f"  {_seat.ink(writer, seat.receipt())}", flush=True)

    # The ladder decides which provider answers; the environment only holds
    # the keys the ladder names. The credentials-file fallback for the
    # Anthropic key survives as an overlay on a *copy* of the environment —
    # `_load_api_key`'s rule (a source, never a destination) still holds.
    env = dict(os.environ)
    if not env.get("ANTHROPIC_API_KEY"):
        file_key = _load_api_key()
        if file_key:
            env["ANTHROPIC_API_KEY"] = file_key
    try:
        inference = InferenceRouter.from_args(
            task_class=args.task_class,
            model=args.model,
            local=args.local,
            writer=writer,
            echo=lambda chunk: print(chunk, end="", flush=True),
            env=env,
        )
    except LadderError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    if not inference.resolution.usable:
        # Say so at the prompt, not on the first turn: every rung of this
        # class is unusable and each has said why.
        print(f"ERROR: no usable rung for class {args.task_class!r}:")
        for v in inference.resolution.skipped:
            print(f"  {v.status:12} {v.reason}")
        sys.exit(1)
    if inference.resolution.forced_unplaced:
        # `--model claude-…` on a ladder whose usable rungs are ollama and
        # groq: refuse here, naming the mismatch, rather than send the id to
        # a rung that cannot serve it and refuse every turn on its 404.
        print(f"ERROR: {inference.resolution.forced_unplaced}")
        print("  pick a --class whose ladder names that dialect, or set its key")
        sys.exit(1)
    first = inference.resolution.usable[0]
    model = first.model or ""

    repo_prompt = _load_system_prompt()
    state = RuntimeState(
        args=args,
        model=model,
        writer=writer,
        history=[],
        # The broker's persona leads and the packet brief follows when this
        # crown is a seat; the repo's CLAUDE.md alone otherwise, as before.
        system_prompt=seat.system_prompt(repo_prompt) if seat else repo_prompt,
        all_tools=_tools.BASE_TOOLS + mcp_extra_tools,
        mcp_names=mcp_names,
        mcp_call=mcp_call,
        client=None,
        policy=policy,
        hooks=hooks,
        inference=inference,
        task_class=args.task_class,
        seat=seat,
    )
    router = CommandRouter(state)
    start_receipt = _grove.session_started(writer.session_id, model)
    state.hooks.run_event(
        "SessionStart", {"session_id": writer.session_id, "model": model}
    )
    if not start_receipt.skipped and not start_receipt.ok:
        print(f"  [grove] {start_receipt.detail}", flush=True)

    trust_label = "trust=on" if args.trust else "trust=off"
    seat_label = f"  [seat: {seat.app_id}/{seat.entry_mode}]" if seat else ""
    print(
        f"\nRatatosk  [{args.task_class}: {first.rung.name}/{model}]  [{trust_label}]"
        f"{seat_label}  session:{writer.session_id[:8]}…"
    )
    print("Type /help for commands.\n")

    try:
        while True:
            try:
                user_input = input("▶ ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                if router.run(user_input):
                    break
                continue
            try:
                _run_turn(state, user_input)
            except KeyboardInterrupt:
                # An interrupt ends the turn, not the session. KeyboardInterrupt
                # is a BaseException, so the `except Exception` below never saw
                # it and it escaped the loop entirely — taking every cleanup
                # path with it. The moment you most want to interrupt was the
                # one that cost you the session.
                print("\n  [interrupted] turn abandoned — session still open")
            except Exception as exc:
                print(f"\nERROR: {exc}")
    finally:
        _shutdown(state)


if __name__ == "__main__":
    main()
