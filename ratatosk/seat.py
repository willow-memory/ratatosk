"""Crown as a seat: enter through willow-mcp, carry its persona, hand off at close.

Sealed decision 3613d55e: a woken seat's runtime is crown, run *with the
seat's app_id and the packet's dispatch_id*, and every wake inks a receipt
beside the heartbeat with the handoff id. This module is the seat half of
that — everything crown does between "the MCP transport is up" and "the
first turn", and again between "the last turn" and "the JSONL is indexed".

Three rules the broker already enforces and this side must not undercut:

* ``session_enter`` with a ``dispatch_id`` already moves the packet to
  ``working``; calling ``dispatch_accept`` afterwards is ``invalid_transition``.
  So: enter, never accept.
* The orchestrator seat is human-only (``whoami`` says ``human_only``). A
  crown that a daemon started is a specialist; ``--app-id willow`` is refused
  here unless the environment already says a human is in the chair.
* ``verify_handoff`` refuses prose evidence — a finding must carry a
  non-empty ``evidence`` list — and the lint-claim judge refuses a clean
  claim that names no version. The closeout this module builds says only
  what the transcript can back, and never says "clean" at all.

Everything through ``mcp_call`` returns a *string* (``mcp_client.call``):
one or several JSON documents, or the ``[mcp-error]`` sentinel. Both shapes
are read here; a transport error is an ``error`` like any other.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ratatosk import grove as _grove
from ratatosk.mcp_client import MCP_ERROR_PREFIX, decode_payloads

ORCHESTRATOR_APP_ID = "willow"
ORCHESTRATOR_FLAG = "WILLOW_HUMAN_ORCHESTRATOR"

#: What a dispatched seat closes with, and what a human-entry seat closes
#: with. The broker names one of these in ``closeout_tools``; when it names
#: neither, the entry_mode decides.
CLOSEOUT_DISPATCH = "handoff_write_v4"
CLOSEOUT_HUMAN = "session_handoff_write"


class SeatRefused(Exception):
    """The seat could not be taken. The message is the operator-facing reason."""


@dataclass
class SeatEntry:
    """What ``session_enter`` gave back, kept to what the runtime needs."""

    app_id: str
    session_id: str
    dispatch_id: str | None
    entry_mode: str
    persona: str
    job: str
    not_job: str
    assignment: str
    closeout_tool: str
    blockers: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def persona_sha256(self) -> str:
        """Hash of the persona AS USED — the stripped text that heads the
        system prompt — so the receipt names the prompt bytes, not the
        broker's trailing newline."""
        return hashlib.sha256(self.persona.strip().encode("utf-8")).hexdigest()

    def system_prompt(self, repo_prompt: str) -> str:
        """The broker's persona leads, the packet brief follows, the repo's
        CLAUDE.md comes last. Order is the point: the seat is who the broker
        says it is, working the packet it was given, in the repo's house
        style — not the other way round."""
        parts = [p for p in (self.persona.strip(), self.assignment.strip()) if p]
        parts.append(repo_prompt)
        return "\n\n".join(parts)

    def receipt(self) -> dict:
        return {
            "event": "seat_entered",
            "app_id": self.app_id,
            "session_id": self.session_id,
            "dispatch_id": self.dispatch_id,
            "entry_mode": self.entry_mode,
            "persona_sha256": self.persona_sha256,
            "closeout_tool": self.closeout_tool,
        }


def decode_result(result: Any) -> dict:
    """One dict out of whatever ``mcp_call`` handed back.

    A transport failure (the ``[mcp-error]`` sentinel) becomes
    ``{"error": <text>}`` so every caller reads one shape. Several documents
    collapse to the first dict — the entry and closeout verbs answer with one.
    """
    if isinstance(result, dict):
        return result
    if result is None:
        return {"error": "no result"}
    text = str(result).strip()
    if not text:
        return {"error": "empty result"}
    if text.startswith(MCP_ERROR_PREFIX):
        return {"error": text}
    for payload in decode_payloads(text):
        if isinstance(payload, dict):
            return payload
    return {"error": f"unparseable result: {text[:200]}"}


def check_app_id(app_id: str, env=None) -> None:
    """Refuse the orchestrator seat unless a human already holds it.

    ``WILLOW_HUMAN_ORCHESTRATOR=1`` is what the desk's own ``.mcp.json``
    sets; a daemon-run crown never has it, and must not grant it to itself.
    """
    env = os.environ if env is None else env
    if app_id == ORCHESTRATOR_APP_ID and env.get(ORCHESTRATOR_FLAG, "") != "1":
        raise SeatRefused(
            f"--app-id {ORCHESTRATOR_APP_ID} is the human orchestrator seat "
            f"(whoami human_only); a crown may take it only when "
            f"{ORCHESTRATOR_FLAG}=1 is already in the environment"
        )


def enter(
    mcp_call,
    *,
    app_id: str,
    session_id: str,
    dispatch_id: str | None = None,
    project: str = "",
    workspace: str = "",
) -> SeatEntry:
    """``session_enter`` as the seat. Raises ``SeatRefused`` on an error result
    or on blockers — the loop must not start in either case."""
    inputs: dict[str, Any] = {"app_id": app_id, "session_id": session_id}
    if dispatch_id:
        inputs["dispatch_id"] = dispatch_id
    if project:
        inputs["project"] = project
    if workspace:
        inputs["workspace"] = workspace
    result = decode_result(mcp_call("session_enter", inputs))
    if result.get("error"):
        raise SeatRefused(f"session_enter refused: {result['error']}")

    blockers = _blockers(result)
    entry_mode = str(result.get("entry_mode") or "")
    if blockers:
        lines = "; ".join(
            f"{b.get('id', '?')}: {b.get('summary', '')}" for b in blockers
        )
        # A specialist with blockers cannot do its packet — refuse before a
        # turn is spent. The human seat runs with blockers listed: the desk
        # itself does (an expired lease is the usual one), and refusing it
        # here would lock the operator out of their own chair. Printed, kept
        # on the entry, never silent.
        if not _is_human_entry(entry_mode):
            raise SeatRefused(
                f"session_enter for {app_id} carries {len(blockers)} blocker(s): {lines}"
            )
        print(
            f"  [seat] {app_id} enters with {len(blockers)} blocker(s): {lines}",
            flush=True,
        )

    closeout = _closeout_tool(result, entry_mode)
    persona = str(result.get("persona") or "")
    return SeatEntry(
        app_id=app_id,
        session_id=session_id,
        dispatch_id=dispatch_id or _dispatch_id(result),
        entry_mode=entry_mode,
        persona=persona,
        job=str(result.get("job") or ""),
        not_job=str(result.get("not_job") or ""),
        assignment=str(result.get("assignment") or ""),
        closeout_tool=closeout,
        blockers=blockers,
        raw=result,
    )


def _is_human_entry(entry_mode: str) -> bool:
    """The broker's human-path modes: ``human`` (a specialist seat opened by a
    person) and ``human_orchestrator`` (the desk). Everything else — ``dispatch``
    above all — is a specialist working a packet."""
    return entry_mode.startswith("human")


def _blockers(result: dict) -> list[dict]:
    raw = result.get("blockers")
    if isinstance(raw, dict):
        items = raw.get("items") or []
        # ``count`` is the broker's own tally; ``items`` is the evidence.
        # Trust the list, and fall back to the count only when the list is
        # missing — an honest empty list with count>0 would be a broker bug,
        # not a reason to refuse the seat on a number alone.
        if not items and int(raw.get("count") or 0) > 0:
            return [{"id": "unlisted", "summary": f"count={raw.get('count')}"}]
        return [b for b in items if isinstance(b, dict)]
    # The orientation block on a human-entry seat nests blockers one level down.
    orientation = result.get("orientation")
    if isinstance(orientation, dict):
        return _blockers(orientation)
    return []


def _dispatch_id(result: dict) -> str | None:
    value = result.get("dispatch_id")
    return str(value) if value else None


def _closeout_tool(result: dict, entry_mode: str) -> str:
    named = result.get("closeout_tools")
    if isinstance(named, list):
        for tool in named:
            if tool in (CLOSEOUT_DISPATCH, CLOSEOUT_HUMAN):
                return str(tool)
    closeout = result.get("closeout")
    if isinstance(closeout, dict) and closeout.get("tool") in (
        CLOSEOUT_DISPATCH,
        CLOSEOUT_HUMAN,
    ):
        return str(closeout["tool"])
    return CLOSEOUT_DISPATCH if entry_mode == "dispatch" else CLOSEOUT_HUMAN


# -- closeout --------------------------------------------------------------


def summarize(entries: list[dict]) -> dict:
    """Roll a session transcript up into what a handoff can carry.

    Reads the JSONL entries ``SessionWriter`` wrote: user/assistant/system
    turns, ``receipt`` rows (``TurnReceipt.as_dict``), and ``tool`` rows
    (``SessionWriter.write_tool`` — one per dispatched tool call, name and
    result size only, written by ``crown._run_turn`` beside the terminal's
    ``[tool:name]`` line). A transcript from before that row existed simply
    has no tools to count.
    """
    turns = 0
    tools: Counter[str] = Counter()
    receipts: list[dict] = []
    last_assistant = ""
    for entry in entries:
        etype = entry.get("type")
        if etype == "user":
            content = entry.get("message", {}).get("content")
            if isinstance(content, str):
                turns += 1
        elif etype == "assistant":
            content = entry.get("message", {}).get("content")
            if isinstance(content, str) and content.strip():
                last_assistant = content.strip()
        elif etype == "receipt":
            receipt = entry.get("receipt")
            # Turn receipts carry ``outcome`` (ratatosk.inference); the seat's
            # own entered/closed rows carry ``event`` and are not model calls
            # — counting them as refused turns is how "1 ok, 1 refused" came
            # out of a session with one call.
            if isinstance(receipt, dict) and "outcome" in receipt:
                receipts.append(receipt)
        elif etype == "tool":
            name = entry.get("name")
            if name:
                tools[str(name)] += 1

    ok = [r for r in receipts if r.get("outcome") == "ok"]
    refused = [r for r in receipts if r.get("outcome") != "ok"]
    rungs: Counter[str] = Counter(
        f"{r.get('provider')}/{r.get('model')}" for r in ok if r.get("provider")
    )
    tokens_in = sum(r["tokens_in"] for r in ok if isinstance(r.get("tokens_in"), int))
    tokens_out = sum(
        r["tokens_out"] for r in ok if isinstance(r.get("tokens_out"), int)
    )
    return {
        "turns": turns,
        "tools": dict(sorted(tools.items())),
        "calls_ok": len(ok),
        "calls_refused": len(refused),
        "rungs": dict(sorted(rungs.items())),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "next_bite": last_assistant,
    }


def build_findings(summary: dict, entry: SeatEntry, jsonl_path: str) -> list[dict]:
    """Findings the verifier will accept: each carries an ``evidence`` list
    that names a checkable thing — the JSONL path, a count from the
    receipts. Nothing here claims a test result or a lint result; crown did
    not run any, and a claim it cannot back is exactly what the gate refuses.
    """
    # A listening seat has no REPL and no JSONL; its session id is the record.
    transcript = (
        f"transcript: {jsonl_path}"
        if jsonl_path
        else f"no transcript (listener); session: {entry.session_id}"
    )
    findings = [
        {
            "title": f"{entry.app_id} worked the session as {entry.entry_mode}",
            "severity": "info",
            "evidence": [
                transcript,
                f"turns: {summary['turns']}",
                f"model calls: {summary['calls_ok']} ok, {summary['calls_refused']} refused",
            ],
        }
    ]
    if summary["rungs"]:
        findings.append(
            {
                "title": "inference rungs used",
                "severity": "info",
                "evidence": [
                    f"{where}: {count} call(s)"
                    for where, count in summary["rungs"].items()
                ]
                + [f"tokens: {summary['tokens_in']} in / {summary['tokens_out']} out"],
            }
        )
    if summary["tools"]:
        findings.append(
            {
                "title": "tools called",
                "severity": "info",
                "evidence": [f"{name}: {n}" for name, n in summary["tools"].items()],
            }
        )
    return findings


def narrative(summary: dict, entry: SeatEntry) -> str:
    head = (
        f"{entry.app_id} ({entry.entry_mode}) — {summary['turns']} turn(s), "
        f"{summary['calls_ok']} model call(s) ok, {summary['calls_refused']} refused."
    )
    if summary["tools"]:
        head += f" Tools: {sum(summary['tools'].values())} call(s)."
    if entry.dispatch_id:
        head = f"Dispatch {entry.dispatch_id}. " + head
    if summary["rungs"]:
        head += " Rungs: " + ", ".join(f"{w} ×{n}" for w, n in summary["rungs"].items())
    return head


def close(mcp_call, entry: SeatEntry, entries: list[dict], jsonl_path: str) -> dict:
    """Call the closeout the broker named. Returns the decoded result — an
    ``error`` key on refusal — and never raises: the caller inks whichever."""
    summary = summarize(entries)
    findings = build_findings(summary, entry, jsonl_path)
    text = narrative(summary, entry)
    if entry.closeout_tool == CLOSEOUT_DISPATCH:
        if not entry.dispatch_id:
            return {
                "error": "handoff_write_v4 needs a dispatch_id and the seat has none"
            }
        inputs: dict[str, Any] = {
            "app_id": entry.app_id,
            "dispatch_id": entry.dispatch_id,
            "findings": findings,
            "narrative": text,
            # Crown cannot know the packet's checklist is done; it only knows
            # the session ended. A false "resolved" is the claim the verifier
            # exists to refuse, so this side never makes it.
            "checklist_resolved": False,
            "envelope_clean": True,
        }
    else:
        inputs = {
            "app_id": entry.app_id,
            "session_id": entry.session_id,
            "narrative": text,
            "summary": text,
            "findings": findings,
            "next_bite": summary["next_bite"],
        }
        project = os.environ.get("WILLOW_HANDOFF_PROJECT", "")
        if project:
            inputs["project"] = project
    try:
        return decode_result(mcp_call(entry.closeout_tool, inputs))
    except Exception as exc:  # the transport raised instead of answering
        return {"error": f"{entry.closeout_tool} raised: {exc}"}


def closed_receipt(entry: SeatEntry, result: dict) -> dict:
    receipt = {
        "event": "seat_closed",
        "app_id": entry.app_id,
        "session_id": entry.session_id,
        "dispatch_id": entry.dispatch_id,
        "closeout_tool": entry.closeout_tool,
    }
    if result.get("error"):
        receipt["error"] = str(result["error"])
    else:
        receipt["handoff_id"] = str(
            result.get("handoff_id")
            or result.get("id")
            or result.get("path")
            or result.get("continuity_key")
            or "ok"
        )
    return receipt


def ink(writer, receipt: dict) -> str:
    """Grove first, JSONL second, so the row records whether the bus got it.
    Same discipline as ``inference._ink``; never raises."""
    line = _line(receipt)
    try:
        posted = _grove.seat_receipt(line)
    except Exception as exc:
        posted = _grove.GroveReceipt(ok=False, detail=f"raised: {exc}")
    if posted.skipped:
        receipt["grove"] = "skipped"
    elif posted.ok:
        receipt["grove"] = "posted"
    else:
        receipt["grove"] = f"refused: {posted.detail}"
        print(f"  [seat] grove post refused: {posted.detail}", flush=True)
    if writer is not None:
        try:
            writer.write_receipt(receipt)
        except Exception as exc:
            print(f"  [seat] receipt not written: {exc}", flush=True)
    return line


def _line(receipt: dict) -> str:
    event = receipt.get("event")
    who = receipt.get("app_id")
    sid = str(receipt.get("session_id") or "")[:8]
    disp = receipt.get("dispatch_id")
    tail = f" dispatch={disp}" if disp else ""
    if event == "seat_entered":
        return (
            f"[ratatosk] seat entered app={who} session={sid}{tail} "
            f"mode={receipt.get('entry_mode')} persona={str(receipt.get('persona_sha256'))[:12]}"
        )
    if receipt.get("error"):
        return f"[ratatosk] seat close FAILED app={who} session={sid}{tail}: {receipt['error']}"
    return (
        f"[ratatosk] seat closed app={who} session={sid}{tail} "
        f"via={receipt.get('closeout_tool')} handoff={receipt.get('handoff_id')}"
    )
