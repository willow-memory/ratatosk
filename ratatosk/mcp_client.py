"""MCP stdio client — defaults to willow-mcp, not archived sap paths.

Field names differ across mcp SDK majors: 1.x exposes ``Tool.inputSchema`` and
``CallToolResult.isError``, 2.x renames the Python attributes to
``input_schema`` and ``is_error`` (the wire aliases are unchanged). Read both,
so one venv upgrade does not silently sever the fleet.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import threading
from pathlib import Path

MCP_ERROR_PREFIX = "[mcp-error]"

_mcp_session = None
_mcp_loop: asyncio.AbstractEventLoop | None = None
_mcp_stop_event: asyncio.Event | None = None
_mcp_thread: threading.Thread | None = None


def default_mcp_argv() -> list[str]:
    override = os.environ.get("RATATOSK_MCP_COMMAND", "").strip()
    if override:
        return shlex.split(override)
    module = os.environ.get("RATATOSK_MCP_MODULE", "willow_mcp")
    return [sys.executable, "-m", module]


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
    env.update(
        {k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIXES)}
    )
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

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            _mcp_session = session
            ready.set()
            await stop.wait()


def start(argv: list[str] | None = None) -> tuple[list[dict], set[str]]:
    global _mcp_loop, _mcp_thread

    argv = argv or default_mcp_argv()
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


def call(name: str, inputs: dict) -> str:
    try:
        result = _mcp_call_sync(_mcp_session.call_tool(name, inputs))
        if _is_error(result):
            return f"{MCP_ERROR_PREFIX} {result.content}"
        parts = [chunk.text for chunk in result.content if hasattr(chunk, "text")]
        return "\n".join(parts) if parts else json.dumps(str(result.content))
    except Exception as exc:
        return f"{MCP_ERROR_PREFIX} {exc}"


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
