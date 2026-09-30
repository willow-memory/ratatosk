"""MCP stdio client — defaults to willow-mcp, not archived sap paths.

Field names differ across mcp SDK majors: 1.x exposes ``Tool.inputSchema`` and
``CallToolResult.isError``, 2.x renames the Python attributes to
``input_schema`` and ``is_error`` (the wire aliases are unchanged). Read both,
so one venv upgrade does not silently sever the fleet.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import sys
import threading
import time

MCP_ERROR_PREFIX = "[mcp-error]"

_mcp_session = None
_mcp_loop: asyncio.AbstractEventLoop | None = None
_mcp_stop_event: asyncio.Event | None = None
_mcp_thread: threading.Thread | None = None

#: What `start` was called with, so a reconnect can repeat it exactly.
_mcp_argv: list[str] | None = None
_last_reconnect: float = 0.0

#: Seconds between reconnect attempts. A server that is down stays down for a
#: while, and a listener polling every 2s would otherwise respawn it — and its
#: Postgres connections — dozens of times a minute while it fails.
RECONNECT_COOLDOWN = 15.0


def default_mcp_argv() -> list[str]:
    """The command that spawns willow-mcp.

    ``RATATOSK_MCP_COMMAND`` wins outright when set. Otherwise the
    interpreter is ``WILLOW_MCP_PYTHON`` (the broker's own venv python,
    resolved per-box) when set, falling back to ``sys.executable`` — THIS
    process's own interpreter — only when it is not. That fallback used to
    be the only answer: a woken seat's own venv (ratatosk's) has no
    ``willow_mcp`` package installed, so ``sys.executable -m willow_mcp``
    failed at import, the MCP init handshake timed out, and the daemon spun
    in ``Restart=on-failure`` forever (Loki FC9EDFB8 finding 3) — the very
    env-file key (``WILLOW_MCP_PYTHON``) the daemon's own unit template
    already kept was simply never read.
    """
    override = os.environ.get("RATATOSK_MCP_COMMAND", "").strip()
    if override:
        return shlex.split(override)
    module = os.environ.get("RATATOSK_MCP_MODULE", "willow_mcp")
    python = os.environ.get("WILLOW_MCP_PYTHON", "").strip() or sys.executable
    return [python, "-m", module]


#: Environment namespaces forwarded to the server we spawn. The MCP SDK does
#: NOT inherit the parent environment: with ``StdioServerParameters.env`` unset
#: it hands the child ``get_default_environment()``, which is HOME, LOGNAME,
#: PATH, SHELL, TERM, USER and nothing else. Every WILLOW_* variable was being
#: stripped, so the willow-mcp we launched fell back to WILLOW_PG_DB="willow"
#: instead of the fleet's willow_20 and reported postgres_unavailable for every
#: call — while the same server, launched from .mcp.json with a full env,
#: worked. WILLOW_HOME was stripped too, so it never read our signed manifest.
_ENV_PREFIXES = ("WILLOW_", "RATATOSK_", "PG")


def server_env() -> dict[str, str]:
    """The environment to hand the spawned MCP server.

    SDK defaults plus this process's fleet configuration. Set
    ``RATATOSK_MCP_INHERIT_ENV=1`` to forward the whole environment instead.
    """
    from mcp.client.stdio import get_default_environment

    if os.environ.get("RATATOSK_MCP_INHERIT_ENV", "").strip() in {"1", "true", "yes"}:
        return dict(os.environ)
    env = dict(get_default_environment())
    env.update({k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIXES)})
    return env


def _mcp_call_sync(coro):
    assert _mcp_loop is not None
    return asyncio.run_coroutine_threadsafe(coro, _mcp_loop).result(timeout=60)


async def _lifecycle(argv: list[str], ready: threading.Event) -> None:
    global _mcp_session, _mcp_stop_event
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    stop = asyncio.Event()
    _mcp_stop_event = stop
    params = StdioServerParameters(command=argv[0], args=argv[1:], env=server_env())

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        _mcp_session = session
        ready.set()
        await stop.wait()


def start(argv: list[str] | None = None) -> tuple[list[dict], set[str]]:
    global _mcp_loop, _mcp_thread, _mcp_argv

    argv = argv or default_mcp_argv()
    _mcp_argv = argv  # remembered so reconnect() can repeat it exactly
    loop = asyncio.new_event_loop()
    _mcp_loop = loop
    ready = threading.Event()

    _mcp_thread = threading.Thread(
        target=lambda: loop.run_until_complete(_lifecycle(argv, ready)),
        daemon=True,
        name="ratatosk-mcp",
    )
    _mcp_thread.start()

    if not ready.wait(timeout=60):
        raise RuntimeError("MCP server did not initialize within 60s")

    tools_result = _mcp_call_sync(_mcp_session.list_tools())
    anthropic_tools, names = [], set()
    for tool in tools_result.tools:
        anthropic_tools.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": _tool_schema(tool),
            }
        )
        names.add(tool.name)
    return anthropic_tools, names


#: Tools the broker answers for any caller, so a seat's grant never removes
#: them from its list (whoami is ungated on the broker side).
_ALWAYS_LISTED = frozenset({"whoami"})


def granted_only(
    tools: list[dict], names: set[str], mcp_call, app_id: str
) -> tuple[list[dict], set[str], str]:
    """Narrow what `start` listed to what `app_id`'s manifest grants.

    The broker lists every registered tool to every caller, so a seat that
    sends the whole list asks for tools its manifest denies (gap 565d2f8251fe:
    ~30 ``gate: 'loki' denied tool`` lines per listener start, burying the
    real wake lines). The broker's own ``whoami`` answers ``tools_allowed`` —
    the exact predicate its gate enforces — so this reads that, never a list
    kept here.

    Returns ``(tools, names, note)``. When ``whoami`` errors or carries no
    ``tools_allowed`` the full list comes back unchanged and ``note`` says
    why: not knowing the grant is no reason to strip a seat of its tools.
    """
    # Local import: seat imports this module at load time.
    from ratatosk.seat import decode_result

    unread = "grant unread ({}); listing unfiltered"
    try:
        answer = decode_result(mcp_call("whoami", {"app_id": app_id}))
    except Exception as exc:
        return tools, names, unread.format(f"whoami raised: {exc}")
    if answer.get("error"):
        return tools, names, unread.format(f"whoami: {answer['error']}")
    allowed = answer.get("tools_allowed")
    if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
        return tools, names, unread.format("whoami named no tools_allowed")
    keep = set(allowed) | _ALWAYS_LISTED
    kept = [t for t in tools if t.get("name") in keep]
    kept_names = {n for n in names if n in keep}
    note = (
        f"{app_id} granted {len(kept)} of {len(tools)} tools "
        f"({len(tools) - len(kept)} not requested)"
    )
    return kept, kept_names, note


def _tool_schema(tool) -> dict:
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    return schema or {}


def decode_payloads(text: str) -> list:
    """Decode the JSON values in a tool result — one document, or several.

    `call` joins the text of every content chunk with a newline, and willow-mcp
    routinely answers with one chunk per row: `grove_get_history` for three
    messages returns three JSON objects, not one array. `json.loads` over the
    whole string raises "Extra data" on exactly that, and both callers treated
    the failure as "nothing here" — the listener parsed a real page of history
    as zero messages, and a failure detail went unread as a successful send.

    Returns every value parsed, in order. An empty list means nothing in the
    string was JSON, which is a different answer from a single `null` and is
    kept distinct so callers can tell them apart.
    """
    decoder = json.JSONDecoder()
    values: list = []
    index, length = 0, len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            value, index = decoder.raw_decode(text, index)
        except ValueError:
            # Trailing content that is not JSON. Keep what did parse rather
            # than discarding a real page over a malformed tail.
            break
        values.append(value)
    return values


def _is_error(result) -> bool:
    flag = getattr(result, "is_error", None)
    if flag is None:
        flag = getattr(result, "isError", None)
    return bool(flag)


def is_live() -> bool:
    """Whether there is a session and a thread still running it."""
    return (
        _mcp_session is not None and _mcp_thread is not None and _mcp_thread.is_alive()
    )


def reconnect() -> bool:
    """Start the server again after it has died. Returns whether it came back.

    Nothing retried before: once the server was gone every `call` returned an
    `[mcp-error]` for the rest of the process. A REPL session ends soon enough
    that a human notices, but `--listen` is meant to run for days on a phone,
    where the first crash silenced it permanently and nothing said so.

    Rate-limited rather than eager. A server that is down tends to stay down,
    and a listener polling every two seconds would otherwise respawn it — and
    its Postgres connections — dozens of times a minute while it fails.
    """
    global _last_reconnect

    if _mcp_argv is None:
        return False  # never started; there is nothing to repeat
    now = time.monotonic()
    if now - _last_reconnect < RECONNECT_COOLDOWN:
        return False
    _last_reconnect = now

    with contextlib.suppress(Exception):
        shutdown(timeout=5.0)  # reap whatever is left of the old one; best effort
    try:
        start(_mcp_argv)
        return is_live()
    except Exception:
        return False


def call(name: str, inputs: dict) -> str:
    try:
        return _call_once(name, inputs)
    except Exception as exc:
        # A tool that *answers* with an error never lands here — that is
        # `_is_error`, handled inside `_call_once`. An exception means the
        # transport failed, which is the only thing worth reconnecting for.
        if not reconnect():
            return f"{MCP_ERROR_PREFIX} {exc}"
        try:
            return _call_once(name, inputs)
        except Exception as retry_exc:
            return f"{MCP_ERROR_PREFIX} {retry_exc} (after reconnect)"


def _call_once(name: str, inputs: dict) -> str:
    result = _mcp_call_sync(_mcp_session.call_tool(name, inputs))
    if _is_error(result):
        return f"{MCP_ERROR_PREFIX} {result.content}"
    parts = [chunk.text for chunk in result.content if hasattr(chunk, "text")]
    return "\n".join(parts) if parts else json.dumps(str(result.content))


def shutdown(timeout: float = 10.0) -> bool:
    """Stop the MCP server and wait for stdio teardown to finish.

    Returns True when the lifecycle thread actually exited. Setting the stop
    event only *schedules* the shutdown; without the join the interpreter can
    exit first — the thread is a daemon — leaving the spawned server's stdio
    torn down by process death rather than by the protocol.
    """
    if _mcp_stop_event is None or _mcp_loop is None:
        return True
    if _mcp_loop.is_closed():
        return True
    try:
        # Event.set is a plain callable, not a coroutine function.
        _mcp_loop.call_soon_threadsafe(_mcp_stop_event.set)
    except RuntimeError:
        # Loop already stopped; nothing left to wake.
        return True
    if _mcp_thread is None:
        return True
    _mcp_thread.join(timeout=timeout)
    return not _mcp_thread.is_alive()
