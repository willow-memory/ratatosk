"""Ratatosk entry point — platform session runtime."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ratatosk import grove as _grove
from ratatosk import session as _session
from ratatosk import sync as _sync
from ratatosk import tools as _tools

_HOME = Path.home()
_WILLOW_ROOT = Path(os.environ.get("WILLOW_ROOT", str(_HOME / "github" / "willow-memory" / "willow")))
_HOOKS_DIR = _HOME / ".claude" / "hooks"
_MAX_TURNS = 20
_MAX_CHARS = 200_000


def _compact(history: list[dict]) -> tuple[list[dict], bool]:
    total = sum(
        len(m["content"]) if isinstance(m["content"], str)
        else sum(len(str(b)) for b in m["content"])
        for m in history
    )
    if total <= _MAX_CHARS and len(history) <= _MAX_TURNS * 2:
        return history, False
    keep = history[-(_MAX_TURNS * 2):]
    dropped = len(history) - len(keep)
    notice = {
        "role": "user",
        "content": (
            f"[System note: {dropped} earlier messages compacted. "
            f"Continuing from turn {len(history) // 2 - _MAX_TURNS + 1}.]"
        ),
    }
    return [notice] + keep, True


def _load_system_prompt() -> str:
    paths = [
        _WILLOW_ROOT / "CLAUDE.md",
        _HOME / "github" / "willow-memory" / "willow" / "CLAUDE.md",
        _HOME / "CLAUDE.md",
    ]
    parts = []
    for path in paths:
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"# {path}\n\n{content}")
    return "\n\n---\n\n".join(parts) if parts else "You are Ratatosk — platform session runtime. ΔΣ=42"


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    for candidate in [
        _HOME / ".ratatosk" / "credentials.json",
        _WILLOW_ROOT / "credentials.json",
    ]:
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                key = data.get("ANTHROPIC_API_KEY", "")
                if key:
                    os.environ["ANTHROPIC_API_KEY"] = key
                    return key
            except Exception:
                pass
    return ""


def _run_hook(script: str | Path, stdin_data: dict | None = None) -> None:
    if not Path(script).exists():
        return
    try:
        inp = json.dumps(stdin_data).encode() if stdin_data else None
        subprocess.run(
            [sys.executable, str(script)],
            input=inp,
            env=os.environ.copy(),
            timeout=10,
            capture_output=True,
            check=False,
        )
    except Exception:
        pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ratatosk — Willow platform session runtime")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--trust", action="store_true", help="Execute tools without per-call confirmation")
    parser.add_argument("--mcp", action="store_true", help="Connect to willow-mcp via stdio")
    parser.add_argument("--local", action="store_true", help="Route to local Ollama")
    parser.add_argument("--listen", action="store_true", help="Run Grove bus listener (requires --mcp)")
    parser.add_argument("--deposit", action="store_true", help="Write tier-0 session deposit on exit")
    args = parser.parse_args()

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

            listener = BusListener(mcp_call=mcp_call)
            listener.run_forever(on_status=lambda msg: print(f"  [listen] {msg}", flush=True))
            return

    if not use_local:
        api_key = _load_api_key()
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY not found.")
            sys.exit(1)
        try:
            import anthropic
        except ImportError:
            print("ERROR: pip install ratatosk-meaning[cloud]")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = None

    all_tools = _tools.BASE_TOOLS + mcp_extra_tools
    system_prompt = _load_system_prompt()
    writer = _session.SessionWriter(cwd=str(Path.cwd()))
    history: list[dict] = []

    start_receipt = _grove.session_started(writer.session_id, model)
    if not start_receipt.skipped and not start_receipt.ok:
        print(f"  [grove] {start_receipt.detail}", flush=True)

    trust_label = "trust=on" if args.trust else "trust=off"
    print(f"\nRatatosk  [{model}]  [{trust_label}]  session:{writer.session_id[:8]}…")
    print("Type /exit, /status, /clear.\n")

    while True:
        try:
            user_input = input("▶ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input == "/exit":
            break
        if user_input == "/status":
            print(f"  session : {writer.session_id}")
            print(f"  turns   : {len(history) // 2}")
            print(f"  jsonl   : {writer.path}")
            receipt = _grove.last_receipt()
            if receipt:
                print(f"  grove   : {receipt.detail}")
            continue
        if user_input == "/clear":
            history = []
            print("  history cleared")
            continue
        if user_input.startswith("/"):
            print(f"  unknown command: {user_input}")
            continue

        writer.write_user(user_input)
        history.append({"role": "user", "content": user_input})

        try:
            if use_local:
                from ratatosk import ollama

                messages = [{"role": "system", "content": system_prompt}] + history
                text = ollama.chat(messages, model=model)
                print(text)
                writer.write_assistant(text)
                history.append({"role": "assistant", "content": text})
            else:
                while True:
                    response_text = ""
                    with client.messages.stream(
                        model=model,
                        max_tokens=8192,
                        system=system_prompt,
                        messages=history,
                        tools=all_tools,
                    ) as stream:
                        for chunk in stream.text_stream:
                            print(chunk, end="", flush=True)
                            response_text += chunk
                        final = stream.get_final_message()

                    print()
                    assistant_content = final.message.content
                    writer.write_assistant(response_text)
                    history.append({"role": "assistant", "content": assistant_content})

                    tool_uses = [b for b in assistant_content if getattr(b, "type", None) == "tool_use"]
                    if not tool_uses:
                        history, compacted = _compact(history)
                        if compacted:
                            print(f"  [compacted — keeping last {_MAX_TURNS} turns]", flush=True)
                        break

                    tool_results = []
                    for tu in tool_uses:
                        result = _tools.prompt_and_dispatch(
                            tu.name, tu.input, args.trust, mcp_names, mcp_call
                        )
                        print(f"  [tool:{tu.name}] → {str(result)[:120]}", flush=True)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu.id,
                                "content": str(result),
                            }
                        )
                    history.append({"role": "user", "content": tool_results})

        except Exception as exc:
            print(f"\nERROR: {exc}")
            continue

    if args.mcp:
        from ratatosk import mcp_client

        mcp_client.shutdown()

    end_receipt = _grove.session_ended(writer.session_id, len(history) // 2, str(writer.path))
    if not end_receipt.skipped and not end_receipt.ok:
        print(f"  [grove] {end_receipt.detail}", flush=True)

    if args.deposit:
        deposit = _sync.write_deposit(writer)
        print(f"  [deposit] {deposit}")

    print(f"Session written: {writer.path}")


if __name__ == "__main__":
    main()
