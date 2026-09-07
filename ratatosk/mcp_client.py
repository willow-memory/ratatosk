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


def default_mcp_argv() -> list[str]:
    override = os.environ.get("RATATOSK_MCP_COMMAND", "").strip()
    if override:
        return shlex.split(override)
    module = os.environ.get("RATATOSK_MCP_MODULE", "willow_mcp")
    return [sys.executable, "-m", module]


def _mcp_call_sync(coro):
    assert _mcp_loop is not None
    return asyncio.run_coroutine_threadsafe(coro, _mcp_loop).result(timeout=60)


async def _lifecycle(argv: list[str], ready: threading.Event) -> None:
    global _mcp_session, _mcp_stop_event
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    stop = asyncio.Event()
    _mcp_stop_event = stop
    params = StdioServerParameters(command=argv[0], args=argv[1:])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            _mcp_session = session
            ready.set()
            await stop.wait()


def start(argv: list[str] | None = None) -> tuple[list[dict], set[str]]:
    global _mcp_loop

    argv = argv or default_mcp_argv()
    loop = asyncio.new_event_loop()
    _mcp_loop = loop
    ready = threading.Event()

    threading.Thread(
        target=lambda: loop.run_until_complete(_lifecycle(argv, ready)),
        daemon=True,
        name="ratatosk-mcp",
    ).start()

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


def shutdown() -> None:
    if _mcp_stop_event is not None and _mcp_loop is not None:
        # Event.set is a plain callable, not a coroutine function.
        _mcp_loop.call_soon_threadsafe(_mcp_stop_event.set)
