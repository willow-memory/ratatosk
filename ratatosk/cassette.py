"""Record a real MCP exchange once; replay it in tests forever.

Every test that needed an MCP result used to invent one — `{"result": [...]}`,
`{"error": "..."}` — plausible shapes nobody had checked against a server. They
were wrong. willow-mcp answers with one content chunk per row, so
`mcp_client.call` returns *several concatenated JSON objects*, not one document
with a `result` key; a real page of history parsed as zero messages and the
listener was a permanent no-op. Hand-written fakes cannot find that, because
they agree with the code by construction.

A cassette is the fix: `call(tool, inputs) -> str` recorded from a live server
and replayed byte-for-byte, so the parsers are tested against what willow-mcp
actually sends.

**Recorded results are scrubbed, not verbatim.** This package is published, and
a real result carries fleet traffic — message bodies, senders, prompts. The
recorder keeps structure (how many objects, which keys, which types) and
replaces the *values* that are payload rather than shape. What a cassette
proves is therefore the shape of a result, which is exactly what the parsers
read, and never the content of one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ratatosk.mcp_client import decode_payloads
from ratatosk.redact import redact

SCHEMA = "ratatosk-cassette/v1"

#: Keys whose values are fleet payload rather than result shape. Their values
#: are replaced on record; the key, the type and the rough length survive,
#: because those are what a parser reads.
DEFAULT_SCRUB_KEYS: frozenset[str] = frozenset(
    {"content", "prompt", "text", "detail", "sender", "first_prompt", "peek", "summary", "note"}
)


class UnrecordedCall(LookupError):
    """A replayed call the cassette has no answer for.

    Raised rather than returning something empty: a test that silently gets
    "no result" for a call nobody recorded is testing the fake, not the code.
    """


def _scrub_value(value: Any, keys: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {k: (_placeholder(v) if k in keys else _scrub_value(v, keys)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v, keys) for v in value]
    if isinstance(value, str):
        return redact(value)
    return value


def _placeholder(value: Any) -> Any:
    """Keep the type and the size; drop the content."""
    if isinstance(value, str):
        return f"<scrubbed {len(value)} chars>"
    return _scrub_value(value, DEFAULT_SCRUB_KEYS)


def scrub_result(text: str, keys: frozenset[str] = DEFAULT_SCRUB_KEYS) -> str:
    """Scrub a raw tool result, preserving how it is laid out on the wire.

    A result that is N concatenated JSON objects stays N concatenated JSON
    objects — that layout is the thing the parsers get wrong, so it is the one
    thing a cassette must not normalise away. Text that is not JSON at all
    (an `[mcp-error]` sentinel, say) is passed through the credential redactor
    and otherwise left alone.
    """
    values = decode_payloads(text)
    if not values:
        return redact(text)
    return "\n".join(json.dumps(_scrub_value(v, keys), indent=2) for v in values)


def _key(tool: str, inputs: dict) -> str:
    return json.dumps([tool, inputs], sort_keys=True)


@dataclass
class Recorder:
    """Wraps a live `mcp_call`, keeping what it saw."""

    inner: Callable[[str, dict], str]
    scrub_keys: frozenset[str] = DEFAULT_SCRUB_KEYS
    interactions: list[dict] = field(default_factory=list)

    def call(self, tool: str, inputs: dict) -> str:
        result = self.inner(tool, inputs)
        self.interactions.append(
            {
                "tool": tool,
                "inputs": _scrub_value(inputs, self.scrub_keys),
                "result": scrub_result(result, self.scrub_keys),
            }
        )
        return result

    def save(self, path: str | Path, *, server: str = "") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "server": server,
                    "scrubbed_keys": sorted(self.scrub_keys),
                    "interactions": self.interactions,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return path


def replay(path: str | Path) -> Callable[[str, dict], str]:
    """An `mcp_call` that answers from a cassette and refuses anything else.

    Repeated calls with the same arguments walk the recorded answers in order
    and then hold on the last one, so a poll loop replays a poll loop rather
    than getting the first page forever.
    """
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} cassette (schema={doc.get('schema')!r})")

    answers: dict[str, list[str]] = {}
    for item in doc["interactions"]:
        answers.setdefault(_key(item["tool"], item["inputs"]), []).append(item["result"])
    seen: dict[str, int] = {}

    def _call(tool: str, inputs: dict) -> str:
        key = _key(tool, inputs)
        recorded = answers.get(key)
        if recorded is None:
            raise UnrecordedCall(
                f"{tool} with {json.dumps(inputs, sort_keys=True)} is not in {path}. "
                f"Re-record it: python -m ratatosk.cassette {path} {tool} '<json inputs>'"
            )
        index = min(seen.get(key, 0), len(recorded) - 1)
        seen[key] = index + 1
        return recorded[index]

    return _call


def _main(argv: list[str] | None = None) -> int:
    """Record a cassette against a live server.

        python -m ratatosk.cassette <path> <tool> '<json inputs>' [<tool> '<json>' ...]

    Starts an MCP connection, makes each call in order, scrubs, and writes the
    cassette. Refreshing a fixture is therefore one command with the calls
    written down in it, rather than a procedure someone has to remember.
    """
    import sys

    from ratatosk import mcp_client

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or (len(args) - 1) % 2:
        print(_main.__doc__)
        return 2

    path, rest = args[0], args[1:]
    pairs = [(rest[i], json.loads(rest[i + 1])) for i in range(0, len(rest), 2)]

    mcp_client.start()
    try:
        recorder = Recorder(inner=mcp_client.call)
        for tool, inputs in pairs:
            recorder.call(tool, inputs)
        written = recorder.save(path, server=" ".join(mcp_client.default_mcp_argv()))
    finally:
        mcp_client.shutdown()

    print(f"recorded {len(pairs)} interaction(s) to {written}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by hand against a live server
    raise SystemExit(_main())
