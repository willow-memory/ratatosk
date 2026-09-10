"""Ratatosk entry point — platform session runtime."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ratatosk import grove as _grove
from ratatosk import session as _session
from ratatosk import sync as _sync
from ratatosk import tools as _tools
from ratatosk.history import ensure_history_db, index_session, list_sessions, load_session_history, search_sessions
from ratatosk.hooks import HookRuntime
from ratatosk.capabilities import CapabilityGate
from ratatosk.permission import check as _permission_check
from ratatosk.policy import PolicyStore, shadowed_rules
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
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tool_use_ids(message: dict) -> set[str]:
    return {b.get("id") for b in _blocks(message) if b.get("type") == "tool_use" and b.get("id")}


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


def _overhead(state: "RuntimeState | None") -> int:
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
        len(m["content"]) if isinstance(m["content"], str) else sum(len(str(b)) for b in m["content"])
        for m in history
    )


def _compact(history: list[dict], state: "RuntimeState | None" = None) -> tuple[list[dict], bool]:
    budget = _MAX_CHARS - _overhead(state)
    if _size(history) <= budget and len(history) <= _MAX_TURNS * 2:
        return history, False

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
        return history, False
    notice = {
        "role": "user",
        "content": f"[System note: compacted {dropped} earlier messages. Continue from current context.]",
    }
    return [notice] + keep, True


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
    return "\n\n---\n\n".join(parts) if parts else "You are Ratatosk — platform session runtime."


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
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                key = data.get("ANTHROPIC_API_KEY", "")
                if key:
                    return key
            except Exception:
                pass
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
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("ANTHROPIC_API_KEY"):
                    return f"file:{candidate}"
            except Exception:
                continue
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
        print(f"  turns      : {len(self.state.history) // 2}")
        print(f"  jsonl      : {self.state.writer.path}")
        print(f"  model      : {self.state.model}")
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
            print(f"  {row.session_id[:8]}  {row.ended_at}  turns={row.turns}  {summary}")
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
        self.state.history = [{"role": "user", "content": note}] + prior[-(_MAX_TURNS * 2) :]
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
        return False

    def _cmd_export(self, arg: str) -> bool:
        target = arg or f"{self.state.writer.session_id}.transcript.txt"
        out = Path(target)
        if not out.is_absolute():
            out = Path.cwd() / out
        out.write_text(_render_transcript(self.state.writer.read_entries()), encoding="utf-8")
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
                print(f"  {len(eaten)} rule(s) never fire — remove them or reorder above the rule that eats them")
            return False
        if parts[0] == "explain" and len(parts) >= 2:
            tool_name = parts[1]
            rest = arg.split(maxsplit=2)[2] if len(parts) > 2 else ""
            # Bash is the one tool the capability gate speaks about, and it
            # classifies the command text — so an explain with no command
            # answers a different question than the real call would.
            inputs = {"command": rest} if tool_name == "Bash" and rest else {}
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
            if tool_name == "Bash" and not rest:
                print("  note: no command given, so the capability gate saw an empty one")
            print("  (dry run — nothing was executed)")
            return False
        if parts[0] == "set" and len(parts) == 3:
            self.state.policy.set_rule(parts[1], parts[2])
            print(f"  rule set: {parts[1]} -> {parts[2]}")
            return False
        if parts[0] == "remove" and len(parts) == 2:
            removed = self.state.policy.remove_rule(parts[1])
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
            message = f"[Compacted local history to last {_MAX_TURNS} turns.]"
            if arg:
                message += f" [{arg}]"
            self.state.writer.write_system(message)
            print("  compacted")
        else:
            print("  no compaction needed")
        self.state.hooks.run_event("PostCompact", {"before": before, "after": len(self.state.history)})
        return False

    def _cmd_help(self, _arg: str) -> bool:
        print("  /exit /clear /status /sessions /resume /doctor /export /permissions /hooks /compact")
        return False


def _run_turn(state: RuntimeState, user_input: str) -> None:
    state.writer.write_user(user_input)
    state.history.append({"role": "user", "content": user_input})

    if state.args.local:
        from ratatosk import ollama

        messages = [{"role": "system", "content": state.system_prompt}] + state.history
        text = ollama.chat(messages, model=state.model)
        print(text)
        state.writer.write_assistant(text)
        state.history.append({"role": "assistant", "content": text})
        state.history, compacted = _compact(state.history, state)
        if compacted:
            print(f"  [compacted — keeping last {_MAX_TURNS} turns]")
        return

    while True:
        response_text = ""
        with state.client.messages.stream(
            model=state.model,
            max_tokens=8192,
            system=state.system_prompt,
            messages=state.history,
            tools=state.all_tools,
        ) as stream:
            for chunk in stream.text_stream:
                print(chunk, end="", flush=True)
                response_text += chunk
            final = stream.get_final_message()

        print()
        assistant_content = final.message.content
        state.writer.write_assistant(response_text)
        state.history.append({"role": "assistant", "content": assistant_content})
        tool_uses = [block for block in assistant_content if getattr(block, "type", None) == "tool_use"]
        if not tool_uses:
            state.history, compacted = _compact(state.history, state)
            if compacted:
                print(f"  [compacted — keeping last {_MAX_TURNS} turns]")
            break

        tool_results = []
        for tu in tool_uses:
            result = _tools.prompt_and_dispatch(
                tu.name,
                tu.input,
                state.args.trust,
                state.mcp_names,
                state.mcp_call,
                policy_store=state.policy,
                hook_runtime=state.hooks,
            )
            print(f"  [tool:{tu.name}] → {str(result)[:120]}", flush=True)
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(result)})
        state.history.append({"role": "user", "content": tool_results})


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

    if state.args.mcp:
        try:
            from ratatosk import mcp_client

            if not mcp_client.shutdown():
                print("  [mcp] stdio teardown did not finish within timeout", flush=True)
        except Exception as exc:
            print(f"  [mcp] shutdown failed: {exc}", flush=True)

    try:
        end_receipt = _grove.session_ended(writer.session_id, turns, str(writer.path))
        if not end_receipt.skipped and not end_receipt.ok:
            print(f"  [grove] {end_receipt.detail}", flush=True)
    except Exception as exc:
        print(f"  [grove] session_ended failed: {exc}", flush=True)

    try:
        state.hooks.run_event("SessionEnd", {"session_id": writer.session_id, "turns": turns})
    except Exception as exc:
        print(f"  [hooks] SessionEnd failed: {exc}", flush=True)

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
    parser = argparse.ArgumentParser(description="Ratatosk — Willow platform session runtime")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--trust", action="store_true", help="Execute tools without per-call confirmation")
    parser.add_argument("--mcp", action="store_true", help="Connect to willow-mcp via stdio")
    parser.add_argument("--local", action="store_true", help="Route to local Ollama")
    parser.add_argument("--listen", action="store_true", help="Run Grove bus listener (requires --mcp)")
    parser.add_argument("--deposit", action="store_true", help="Write tier-0 session deposit on exit")
    args = parser.parse_args()

    # The help text has always said --listen requires --mcp, but nothing
    # enforced it: the listener lives inside `if args.mcp`, so `--listen`
    # alone fell through to an ordinary REPL. Asking for a bus listener and
    # silently getting a chat prompt is the flag lying about what it did.
    if args.listen and not args.mcp:
        parser.error("--listen requires --mcp: the bus listener speaks to willow-mcp over stdio")

    ensure_history_db()
    policy = PolicyStore()
    hooks = HookRuntime()
    use_local = args.local
    model = os.environ.get("OLLAMA_MODEL", "llama3.2:1b") if use_local else args.model

    mcp_names: set[str] = set()
    mcp_extra_tools: list[dict] = []
    mcp_call = None
    if args.mcp:
        print("  [mcp] connecting…", flush=True)
        from ratatosk import mcp_client

        mcp_extra_tools, mcp_names = mcp_client.start()
        mcp_call = mcp_client.call
        _grove.set_grove_sender(_grove.make_mcp_sender(mcp_call))
        print(f"  [mcp] {len(mcp_names)} tools loaded", flush=True)
        if args.listen:
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
                listener.run_forever(on_status=lambda msg: print(f"  [listen] {msg}", flush=True))
            except KeyboardInterrupt:
                print("\n  [listen] stopped", flush=True)
            except ValueError as exc:
                refused = str(exc)
            finally:
                if not mcp_client.shutdown():
                    print("  [mcp] stdio teardown did not finish within timeout", flush=True)
            if refused:
                # Same refusal the termux boot script prints, and the same exit
                # code: an unconfigured listener is a failure, not a quiet no-op.
                print(f"  [listen] {refused}", flush=True)
                raise SystemExit(1)
            return

    if not use_local:
        api_key = _load_api_key()
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY not found.")
            sys.exit(1)
        try:
            import anthropic
        except ImportError:
            print("ERROR: pip install 'willow-ratatosk[cloud]'")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = None

    writer = _session.SessionWriter(cwd=str(Path.cwd()))
    state = RuntimeState(
        args=args,
        model=model,
        writer=writer,
        history=[],
        system_prompt=_load_system_prompt(),
        all_tools=_tools.BASE_TOOLS + mcp_extra_tools,
        mcp_names=mcp_names,
        mcp_call=mcp_call,
        client=client,
        policy=policy,
        hooks=hooks,
    )
    router = CommandRouter(state)
    start_receipt = _grove.session_started(writer.session_id, model)
    state.hooks.run_event("SessionStart", {"session_id": writer.session_id, "model": model})
    if not start_receipt.skipped and not start_receipt.ok:
        print(f"  [grove] {start_receipt.detail}", flush=True)

    trust_label = "trust=on" if args.trust else "trust=off"
    print(f"\nRatatosk  [{model}]  [{trust_label}]  session:{writer.session_id[:8]}…")
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
