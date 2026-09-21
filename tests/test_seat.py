"""Crown as a seat: enter through willow-mcp, carry the persona, hand off at close.

Sealed decision 3613d55e, slice 2. Every test drives a fake ``mcp_call`` that
records what was asked and answers with canned broker results — no willow-mcp
process, no network, no Grove.
"""

from __future__ import annotations

import argparse
import json
import os
from types import SimpleNamespace

import pytest

from ratatosk import crown, grove, mcp_client
from ratatosk import seat as _seat
from ratatosk import session as _session
from ratatosk.crown import RuntimeState, _shutdown
from ratatosk.hooks import HookRuntime
from ratatosk.mcp_client import MCP_ERROR_PREFIX
from ratatosk.policy import PolicyStore

PERSONA = "You are Hanuman — Builder.\n\n*ΔΣ=42*\n"


def _entered(**overrides) -> dict:
    result = {
        "entry_mode": "dispatch",
        "app_id": "hanuman",
        "session_id": "s-1",
        "dispatch_id": "PKT00001",
        "persona": PERSONA,
        "job": "Code, builds, tests, Kart",
        "not_job": "Direct master commits",
        "assignment": "# Build the thing\n\nOne bite.",
        "closeout_tools": ["handoff_write_v4"],
        "blockers": {"count": 0, "items": []},
    }
    result.update(overrides)
    return result


class FakeMCP:
    """Records every call; answers from a table, as JSON text like the real
    ``mcp_client.call`` does."""

    def __init__(self, answers: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.answers = answers or {}

    def __call__(self, name: str, inputs: dict) -> str:
        self.calls.append((name, inputs))
        answer = self.answers.get(name, {"ok": True})
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)

    def named(self, name: str) -> list[dict]:
        return [inputs for n, inputs in self.calls if n == name]


# -- decode_result ---------------------------------------------------------


def test_decode_result_reads_dicts_strings_and_the_transport_sentinel():
    assert _seat.decode_result({"a": 1}) == {"a": 1}
    assert _seat.decode_result('{"a": 1}\n{"b": 2}') == {"a": 1}
    assert _seat.decode_result(f"{MCP_ERROR_PREFIX} boom")["error"].startswith(
        MCP_ERROR_PREFIX
    )
    assert "error" in _seat.decode_result(None)
    assert "error" in _seat.decode_result("   ")
    assert "error" in _seat.decode_result("not json at all")


# -- check_app_id ----------------------------------------------------------


def test_the_orchestrator_seat_is_refused_without_the_human_flag():
    with pytest.raises(_seat.SeatRefused) as info:
        _seat.check_app_id("willow", env={})
    assert "human orchestrator" in str(info.value)
    assert "WILLOW_HUMAN_ORCHESTRATOR" in str(info.value)


def test_the_orchestrator_seat_is_allowed_when_a_human_already_holds_it():
    _seat.check_app_id("willow", env={"WILLOW_HUMAN_ORCHESTRATOR": "1"})


def test_a_specialist_seat_needs_no_flag():
    _seat.check_app_id("hanuman", env={})


# -- enter ------------------------------------------------------------------


def test_enter_calls_session_enter_once_with_the_seat_args():
    mcp = FakeMCP({"session_enter": _entered()})
    entry = _seat.enter(
        mcp,
        app_id="hanuman",
        session_id="s-1",
        dispatch_id="PKT00001",
        project="willows-grove",
        workspace="/tmp/w",
    )
    assert mcp.named("session_enter") == [
        {
            "app_id": "hanuman",
            "session_id": "s-1",
            "dispatch_id": "PKT00001",
            "project": "willows-grove",
            "workspace": "/tmp/w",
        }
    ]
    assert entry.entry_mode == "dispatch"
    assert entry.persona == PERSONA
    assert entry.closeout_tool == "handoff_write_v4"
    assert entry.assignment.startswith("# Build the thing")


def test_enter_never_calls_dispatch_accept():
    """session_enter with a dispatch_id already flips the packet; a second
    transition is invalid_transition."""
    mcp = FakeMCP({"session_enter": _entered()})
    _seat.enter(mcp, app_id="hanuman", session_id="s-1", dispatch_id="PKT00001")
    assert [n for n, _ in mcp.calls] == ["session_enter"]


def test_enter_omits_optional_args_it_was_not_given():
    mcp = FakeMCP({"session_enter": _entered(entry_mode="human", dispatch_id=None)})
    _seat.enter(mcp, app_id="ada", session_id="s-2")
    assert mcp.named("session_enter") == [{"app_id": "ada", "session_id": "s-2"}]


def test_an_error_result_refuses_the_seat():
    mcp = FakeMCP({"session_enter": {"error": "gate denied: 'nobody' not permitted"}})
    with pytest.raises(_seat.SeatRefused) as info:
        _seat.enter(mcp, app_id="nobody", session_id="s-1")
    assert "gate denied" in str(info.value)


def test_a_transport_error_refuses_the_seat():
    mcp = FakeMCP()
    mcp.answers["session_enter"] = RuntimeError("server gone")
    with pytest.raises(_seat.SeatRefused):
        # FakeMCP raises; enter() sees the exception as a transport failure
        # only through decode_result's sentinel path — mirror the real client,
        # which returns the sentinel string instead of raising.
        _seat.enter(
            lambda n, i: f"{MCP_ERROR_PREFIX} server gone",
            app_id="hanuman",
            session_id="s-1",
        )


def test_blockers_refuse_the_seat_and_are_named():
    blocked = _entered(
        blockers={
            "count": 1,
            "items": [{"id": "no_egress_lease", "summary": "no active lease"}],
        }
    )
    mcp = FakeMCP({"session_enter": blocked})
    with pytest.raises(_seat.SeatRefused) as info:
        _seat.enter(mcp, app_id="hanuman", session_id="s-1", dispatch_id="PKT00001")
    assert "1 blocker" in str(info.value)
    assert "no_egress_lease" in str(info.value)


def test_blockers_nested_under_orientation_are_read_too():
    """A specialist opened through the human path still carries its blockers
    one level down; the same shape refuses a dispatch entry."""
    nested = _entered(
        blockers=None,
        orientation={
            "blockers": {
                "count": 1,
                "items": [{"id": "session_unattested", "summary": "x"}],
            }
        },
    )
    del nested["blockers"]
    with pytest.raises(_seat.SeatRefused) as info:
        _seat.enter(
            FakeMCP({"session_enter": nested}),
            app_id="hanuman",
            session_id="s",
            dispatch_id="PKT00001",
        )
    assert "session_unattested" in str(info.value)


def test_the_human_seat_runs_with_blockers_listed(capsys):
    """Loki 32A3263E finding 3: the desk itself runs with an expired lease
    listed; refusing the human path here would lock the operator out of
    their own chair. Printed, kept on the entry, never silent."""
    human = {
        "entry_mode": "human_orchestrator",
        "persona": "You are Willow.",
        "closeout_tools": ["session_handoff_write"],
        "orientation": {
            "blockers": {
                "count": 1,
                "items": [{"id": "no_egress_lease", "summary": "no active lease"}],
            }
        },
    }
    entry = _seat.enter(
        FakeMCP({"session_enter": human}), app_id="willow", session_id="s"
    )
    assert entry.entry_mode == "human_orchestrator"
    assert [b["id"] for b in entry.blockers] == ["no_egress_lease"]
    out = capsys.readouterr().out
    assert "enters with 1 blocker(s)" in out and "no_egress_lease" in out


def test_a_specialist_opened_by_a_person_runs_with_blockers_too():
    entry = _seat.enter(
        FakeMCP(
            {
                "session_enter": _entered(
                    entry_mode="human",
                    dispatch_id=None,
                    closeout_tools=["session_handoff_write"],
                    blockers={"count": 1, "items": [{"id": "x", "summary": "y"}]},
                )
            }
        ),
        app_id="ada",
        session_id="s",
    )
    assert entry.blockers and entry.closeout_tool == "session_handoff_write"


def test_a_count_with_no_items_still_refuses():
    mcp = FakeMCP({"session_enter": _entered(blockers={"count": 2, "items": []})})
    with pytest.raises(_seat.SeatRefused) as info:
        _seat.enter(mcp, app_id="hanuman", session_id="s-1", dispatch_id="PKT00001")
    assert "count=2" in str(info.value)


def test_an_unrequested_dispatch_id_is_not_bound_and_closeout_stays_human(
    tmp_path, monkeypatch, capsys
):
    """Loki 04C311E4: the broker auto-hands the oldest pending packet to a
    bare entry (gap 22c8c1aab079). A seat that asked for no packet must not
    bind it — else a listener's Ctrl-C writes that packet a turns:0 handoff."""
    writer = _transcript(tmp_path, monkeypatch)
    mcp = FakeMCP(
        {
            "session_enter": _entered(dispatch_id="A65EBE16"),
            "session_handoff_write": {"path": "/h/bare.md"},
        }
    )
    entry = _seat.enter(mcp, app_id="hanuman", session_id="s-1")
    assert entry.dispatch_id is None
    assert entry.closeout_tool == "session_handoff_write"
    assert entry.raw["pending_offered"] == "A65EBE16"
    assert entry.receipt()["dispatch_id"] is None
    out = capsys.readouterr().out
    assert "pending dispatch A65EBE16" in out and "not bound" in out
    result = _seat.close(mcp, entry, writer.read_entries(), str(writer.path))
    assert "error" not in result
    assert mcp.named("session_handoff_write")
    assert not mcp.named("handoff_write_v4"), "the offered packet is untouched"


def test_an_unrequested_packet_with_blockers_runs_on_the_human_path(capsys):
    """No packet asked for → the human path decides, whatever entry_mode the
    broker stamped on its auto-handed offer."""
    mcp = FakeMCP(
        {
            "session_enter": _entered(
                blockers={"count": 1, "items": [{"id": "no_lease", "summary": "x"}]}
            )
        }
    )
    entry = _seat.enter(mcp, app_id="hanuman", session_id="s-1")
    assert entry.dispatch_id is None and entry.blockers
    assert "enters with 1 blocker(s)" in capsys.readouterr().out


def test_closeout_follows_the_broker_then_the_entry_mode():
    dispatch = _seat.enter(
        FakeMCP({"session_enter": _entered()}),
        app_id="hanuman",
        session_id="s",
        dispatch_id="PKT00001",
    )
    assert dispatch.closeout_tool == "handoff_write_v4"
    human = _seat.enter(
        FakeMCP(
            {
                "session_enter": _entered(
                    entry_mode="human", closeout_tools=["session_handoff_write"]
                )
            }
        ),
        app_id="ada",
        session_id="s",
    )
    assert human.closeout_tool == "session_handoff_write"
    unnamed = _seat.enter(
        FakeMCP({"session_enter": _entered(entry_mode="human", closeout_tools=[])}),
        app_id="ada",
        session_id="s",
    )
    assert unnamed.closeout_tool == "session_handoff_write"
    nested = _seat.enter(
        FakeMCP(
            {
                "session_enter": _entered(
                    closeout_tools=[], closeout={"tool": "handoff_write_v4"}
                )
            }
        ),
        app_id="hanuman",
        session_id="s",
        dispatch_id="PKT00001",
    )
    assert nested.closeout_tool == "handoff_write_v4"


# -- the system prompt -----------------------------------------------------


def test_the_persona_leads_and_the_repo_prompt_follows():
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s"
    )
    prompt = entry.system_prompt("# CLAUDE.md\n\nrepo rules")
    assert prompt.startswith(PERSONA.strip())
    assert prompt.index("# Build the thing") < prompt.index("repo rules")


def test_no_assignment_means_persona_then_repo():
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered(entry_mode="human", assignment="")}),
        app_id="ada",
        session_id="s",
    )
    assert entry.system_prompt("repo rules") == PERSONA.strip() + "\n\nrepo rules"


# -- receipts --------------------------------------------------------------


def test_the_entry_receipt_carries_the_seat_and_the_persona_hash():
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}),
        app_id="hanuman",
        session_id="s-1",
        dispatch_id="PKT00001",
    )
    receipt = entry.receipt()
    assert receipt["event"] == "seat_entered"
    assert receipt["app_id"] == "hanuman"
    assert receipt["dispatch_id"] == "PKT00001"
    assert receipt["entry_mode"] == "dispatch"
    assert len(receipt["persona_sha256"]) == 64
    assert PERSONA not in json.dumps(receipt), "the hash rides, not the text"


def test_the_persona_hash_names_the_prompt_bytes_not_the_brokers_newline():
    """Loki 32A3263E cosmetic: hash what is used. The system prompt heads
    with the stripped persona; the receipt hashes that same text."""
    import hashlib

    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    prompt = entry.system_prompt("repo")
    used = PERSONA.strip()
    assert prompt.startswith(used), "the stripped persona is what heads the prompt"
    assert entry.persona_sha256 == hashlib.sha256(used.encode("utf-8")).hexdigest()
    assert entry.persona_sha256 != hashlib.sha256(PERSONA.encode("utf-8")).hexdigest()


def test_ink_writes_the_jsonl_row_and_records_the_bus_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    writer = _session.SessionWriter(cwd=str(tmp_path))
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    line = _seat.ink(writer, entry.receipt())
    assert line.startswith("[ratatosk] seat entered app=hanuman")
    rows = [e for e in writer.read_entries() if e["type"] == "receipt"]
    assert rows[0]["receipt"]["event"] == "seat_entered"
    assert rows[0]["receipt"]["grove"] == "skipped"


def test_ink_names_a_refused_grove_post(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "hanuman")
    grove.set_grove_sender(
        lambda _c: grove.GroveReceipt(ok=False, detail="gate denied")
    )
    try:
        writer = _session.SessionWriter(cwd=str(tmp_path))
        entry = _seat.enter(
            FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
        )
        _seat.ink(writer, entry.receipt())
    finally:
        grove.set_grove_sender(None)
    row = next(e for e in writer.read_entries() if e["type"] == "receipt")
    assert row["receipt"]["grove"] == "refused: gate denied"
    assert "grove post refused" in capsys.readouterr().out


# -- summarize / findings --------------------------------------------------


def _transcript(tmp_path, monkeypatch) -> _session.SessionWriter:
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    writer = _session.SessionWriter(cwd=str(tmp_path))
    writer.write_user("do the thing")
    writer.write_receipt(
        {
            "outcome": "ok",
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "tokens_in": 100,
            "tokens_out": 20,
        }
    )
    writer.write_assistant("done: the thing")
    # The rows crown._run_turn writes per dispatched tool — the real writer
    # method, not a hand-shaped dict (Loki 32A3263E finding 2).
    writer.write_tool("willow_web_search", "toolu_1", 512)
    writer.write_tool("store_get", "toolu_2", 88)
    writer.write_tool("willow_web_search", "toolu_3", 0)
    writer.write_user("and another")
    writer.write_receipt({"outcome": "refused", "reason": "ladder exhausted"})
    writer.write_receipt(
        {
            "outcome": "ok",
            "provider": "ollama",
            "model": "llama3.2:3b",
            "tokens_in": "unmeasured",
            "tokens_out": "unmeasured",
        }
    )
    writer.write_assistant("next: open the PR")
    return writer


def test_summarize_rolls_the_transcript_up(tmp_path, monkeypatch):
    writer = _transcript(tmp_path, monkeypatch)
    # The seat's own receipts sit in the same entry type; they are not calls.
    writer.write_receipt({"event": "seat_entered", "app_id": "hanuman"})
    summary = _seat.summarize(writer.read_entries())
    assert summary["turns"] == 2
    assert summary["calls_ok"] == 2
    assert summary["calls_refused"] == 1
    assert summary["rungs"] == {
        "groq/llama-3.3-70b-versatile": 1,
        "ollama/llama3.2:3b": 1,
    }
    assert summary["tokens_in"] == 100 and summary["tokens_out"] == 20
    assert summary["next_bite"] == "next: open the PR"
    assert summary["tools"] == {"store_get": 1, "willow_web_search": 2}
    assert summary["notices"] == []


# -- Loki 3564BE3C F7: the closeout must say how a wake ENDED --------------
#
# summarize() used to ignore `system` rows entirely, so a budget refusal, a
# ladder refusal, a non-interactive confirm refusal, or a wake crash — every
# one of them recorded via `write_system` — left no trace in the handoff.
# An "ok" run and a turn-cap refusal produced the same narrative shape.


def test_summarize_collects_system_notices_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    writer = _session.SessionWriter(cwd=str(tmp_path))
    writer.write_user("work the packet")
    writer.write_system("[budget] turn cap reached (2) — refusing, not stretching")
    writer.write_system("[wake crashed] OSError: boom")
    summary = _seat.summarize(writer.read_entries())
    assert summary["notices"] == [
        "[budget] turn cap reached (2) — refusing, not stretching",
        "[wake crashed] OSError: boom",
    ]


def test_build_findings_surfaces_the_notices_as_their_own_finding(tmp_path, monkeypatch):
    writer = _transcript(tmp_path, monkeypatch)
    writer.write_system("[budget] turn cap reached (2) — refusing, not stretching")
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    summary = _seat.summarize(writer.read_entries())
    findings = _seat.build_findings(summary, entry, str(writer.path))
    (ending,) = [f for f in findings if f["title"] == "how the session ended"]
    assert ending["evidence"] == [
        "[budget] turn cap reached (2) — refusing, not stretching"
    ]


def test_narrative_names_the_last_notice_when_the_session_did_not_end_cleanly(
    tmp_path, monkeypatch
):
    writer = _transcript(tmp_path, monkeypatch)
    writer.write_system("[budget] turn cap reached (2) — refusing, not stretching")
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    summary = _seat.summarize(writer.read_entries())
    text = _seat.narrative(summary, entry)
    assert "Ended: [budget] turn cap reached (2)" in text


def test_narrative_omits_the_ended_clause_when_there_are_no_notices(tmp_path, monkeypatch):
    writer = _transcript(tmp_path, monkeypatch)
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    summary = _seat.summarize(writer.read_entries())
    assert "Ended:" not in _seat.narrative(summary, entry)


# -- Loki 3564BE3C F1: the receipt names what the broker actually returned -


def test_closed_receipt_falls_back_to_the_brokers_status_before_the_literal_ok():
    """The real handoff_write_v4 answer (willow-mcp handoff.py) is
    {dispatch_id, status, reply_to, waiting_for} — no id/path/
    continuity_key at all. The old fallback chain landed on the literal
    string "ok" for every dispatch closeout; it must land on `status` first."""
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    receipt = _seat.closed_receipt(
        entry,
        {
            "dispatch_id": "PKT00001",
            "status": "complete",
            "reply_to": "willow",
            "waiting_for": "verify_handoff",
        },
    )
    assert receipt["handoff_id"] == "complete"


def test_closed_receipt_still_falls_back_to_ok_with_nothing_at_all():
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    receipt = _seat.closed_receipt(entry, {})
    assert receipt["handoff_id"] == "ok"


def test_run_turn_writes_a_tool_row_the_closeout_can_count(tmp_path, monkeypatch):
    """End to end: a dispatched tool call in _run_turn lands as a `tool` row in
    the JSONL — name, id and result size, never the arguments or the result —
    and summarize() counts it. Before this row the transcript did not know a
    tool had been called at all, and the tools finding was dead on a real
    session."""
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    writer = _session.SessionWriter(cwd=str(tmp_path))

    tool_use = {"id": "toolu_9", "name": "store_get", "input": {"k": "v"}}
    calling = SimpleNamespace(
        text="", blocks=[{"type": "tool_use", **tool_use}], tool_uses=[tool_use]
    )
    done = SimpleNamespace(
        text="ok", blocks=[{"type": "text", "text": "ok"}], tool_uses=[]
    )
    receipt = SimpleNamespace(rung="r", line=lambda: "[ratatosk] turn ok")
    answers = iter([(calling, receipt), (done, receipt)])
    inference = SimpleNamespace(current=None, complete=lambda *_a: next(answers))

    state = RuntimeState(
        args=argparse.Namespace(
            trust=True, mcp=False, listen=False, deposit=False, local=True
        ),
        model="m",
        writer=writer,
        history=[],
        system_prompt="x",
        all_tools=[],
        mcp_names=set(),
        mcp_call=None,
        client=None,
        policy=PolicyStore(),
        hooks=HookRuntime(),
        inference=inference,
    )
    monkeypatch.setattr(
        crown._tools,
        "prompt_and_dispatch",
        lambda name, inp, *a, **kw: "SECRET-RESULT-xyz",
    )
    crown._run_turn(state, "look it up")
    rows = [e for e in writer.read_entries() if e["type"] == "tool"]
    assert rows == [
        {
            **{
                k: rows[0][k]
                for k in (
                    "uuid",
                    "parentUuid",
                    "timestamp",
                    "isSidechain",
                    "sessionId",
                    "cwd",
                    "version",
                )
            },
            "type": "tool",
            "name": "store_get",
            "tool_use_id": "toolu_9",
            "result_chars": len("SECRET-RESULT-xyz"),
        }
    ]
    assert _transcript_leaks(writer.path, "SECRET-RESULT-xyz", '"k": "v"') == []
    assert _seat.summarize(writer.read_entries())["tools"] == {"store_get": 1}


def _transcript_leaks(path, *secrets: str) -> list[str]:
    """The secrets that DID reach the JSONL on disk — a tool row carries the
    name and the result size, never the arguments or the result. Empty means
    nothing leaked."""
    text = path.read_text(encoding="utf-8")
    return [s for s in secrets if s in text]


def test_the_transcript_leak_scan_fires_on_a_planted_secret(tmp_path):
    """Planted: a row that carries the result verbatim must be reported by
    the helper the tool-row test relies on."""
    leaky = tmp_path / "leaky.jsonl"
    leaky.write_text('{"type":"tool","result":"SECRET-RESULT-xyz"}\n', encoding="utf-8")
    assert _transcript_leaks(leaky, "SECRET-RESULT-xyz", "never-there") == [
        "SECRET-RESULT-xyz"
    ]


def test_findings_each_carry_evidence_and_never_claim_a_test_or_lint_result(
    tmp_path, monkeypatch
):
    writer = _transcript(tmp_path, monkeypatch)
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    findings = _seat.build_findings(
        _seat.summarize(writer.read_entries()), entry, str(writer.path)
    )
    assert findings, "at least the session finding"
    for f in findings:
        assert f.get("title"), "verify_handoff needs a text key"
        assert isinstance(f["evidence"], list) and f["evidence"]
        assert all(str(e).strip() for e in f["evidence"])
    text = json.dumps(findings).lower()
    assert "passed" not in text and "ruff" not in text and "clean" not in text
    # Compared at the evidence level, not on the escaped blob: a Windows tmp
    # path has an uppercase drive letter and backslashes json.dumps doubles.
    assert any(str(writer.path) in str(e) for f in findings for e in f["evidence"])


# -- close -----------------------------------------------------------------


def test_close_on_the_dispatch_path_calls_handoff_write_v4(tmp_path, monkeypatch):
    writer = _transcript(tmp_path, monkeypatch)
    mcp = FakeMCP(
        {"session_enter": _entered(), "handoff_write_v4": {"dispatch_id": "PKT00001"}}
    )
    entry = _seat.enter(mcp, app_id="hanuman", session_id="s-1", dispatch_id="PKT00001")
    result = _seat.close(mcp, entry, writer.read_entries(), str(writer.path))
    assert "error" not in result
    (call,) = mcp.named("handoff_write_v4")
    assert call["app_id"] == "hanuman"
    assert call["dispatch_id"] == "PKT00001"
    assert call["checklist_resolved"] is False, "crown cannot vouch for the checklist"
    assert call["envelope_clean"] is True
    assert call["findings"][0]["evidence"]
    assert "2 turn(s)" in call["narrative"]
    assert not mcp.named("session_handoff_write")


def test_close_on_the_human_path_calls_session_handoff_write(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HANDOFF_PROJECT", "willows-grove")
    writer = _transcript(tmp_path, monkeypatch)
    mcp = FakeMCP(
        {
            "session_enter": _entered(
                entry_mode="human",
                dispatch_id=None,
                closeout_tools=["session_handoff_write"],
            ),
            "session_handoff_write": {"path": "/handoffs/x.md"},
        }
    )
    entry = _seat.enter(mcp, app_id="ada", session_id="s-9")
    _seat.close(mcp, entry, writer.read_entries(), str(writer.path))
    (call,) = mcp.named("session_handoff_write")
    assert call["app_id"] == "ada" and call["session_id"] == "s-9"
    assert call["next_bite"] == "next: open the PR"
    assert call["project"] == "willows-grove"
    assert call["findings"][0]["evidence"]
    assert not mcp.named("handoff_write_v4")


def test_close_reports_a_dispatch_closeout_with_no_dispatch_id(tmp_path, monkeypatch):
    writer = _transcript(tmp_path, monkeypatch)
    # enter() no longer produces this shape (an unrequested packet stays on
    # the human path); close() still guards it for an entry built elsewhere.
    mcp = FakeMCP()
    entry = _seat.SeatEntry(
        app_id="hanuman",
        session_id="s-1",
        dispatch_id=None,
        entry_mode="dispatch",
        persona=PERSONA,
        job="",
        not_job="",
        assignment="",
        closeout_tool="handoff_write_v4",
    )
    result = _seat.close(mcp, entry, writer.read_entries(), str(writer.path))
    assert "needs a dispatch_id" in result["error"]
    assert not mcp.named("handoff_write_v4")


def test_close_returns_the_brokers_refusal_rather_than_raising(tmp_path, monkeypatch):
    writer = _transcript(tmp_path, monkeypatch)
    mcp = FakeMCP(
        {
            "session_enter": _entered(),
            "handoff_write_v4": {"error": "dispatch not in working state"},
        }
    )
    entry = _seat.enter(mcp, app_id="hanuman", session_id="s-1", dispatch_id="PKT00001")
    result = _seat.close(mcp, entry, writer.read_entries(), str(writer.path))
    assert result["error"] == "dispatch not in working state"


def test_closed_receipt_carries_the_handoff_id_or_the_error():
    entry = _seat.enter(
        FakeMCP({"session_enter": _entered()}), app_id="hanuman", session_id="s-1"
    )
    ok = _seat.closed_receipt(
        entry, {"dispatch_id": "PKT00001", "path": "/p/handoff.json"}
    )
    assert ok["event"] == "seat_closed" and ok["handoff_id"] == "/p/handoff.json"
    bad = _seat.closed_receipt(entry, {"error": "refused"})
    assert bad["error"] == "refused" and "handoff_id" not in bad
    assert "FAILED" in _seat._line(bad)


# -- _shutdown -------------------------------------------------------------


def _state(tmp_path, monkeypatch, *, seat, mcp_call) -> RuntimeState:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    args = argparse.Namespace(
        local=True, trust=False, mcp=True, listen=False, deposit=False
    )
    writer = _session.SessionWriter(cwd=str(tmp_path))
    writer.write_user("hello")
    return RuntimeState(
        args=args,
        model="m",
        writer=writer,
        history=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
        system_prompt="x",
        all_tools=[],
        mcp_names=set(),
        mcp_call=mcp_call,
        client=None,
        policy=PolicyStore(),
        hooks=HookRuntime(),
        seat=seat,
    )


def test_shutdown_closes_the_seat_before_the_transport_goes(
    tmp_path, monkeypatch, capsys
):
    order: list[str] = []
    mcp = FakeMCP({"session_enter": _entered(), "handoff_write_v4": {"ok": True}})
    entry = _seat.enter(mcp, app_id="hanuman", session_id="s-1", dispatch_id="PKT00001")

    def recording_call(name, inputs):
        order.append(name)
        return mcp(name, inputs)

    def shutdown_(*a, **kw):
        order.append("shutdown")
        return True

    monkeypatch.setattr(mcp_client, "shutdown", shutdown_)
    monkeypatch.setattr(crown, "index_session", lambda **kw: order.append("index"))
    state = _state(tmp_path, monkeypatch, seat=entry, mcp_call=recording_call)
    _shutdown(state)
    assert (
        order.index("handoff_write_v4") < order.index("shutdown") < order.index("index")
    )
    rows = [e["receipt"] for e in state.writer.read_entries() if e["type"] == "receipt"]
    assert rows[-1]["event"] == "seat_closed" and "handoff_id" in rows[-1]
    assert "seat closed app=hanuman" in capsys.readouterr().out


def test_a_failed_closeout_is_printed_inked_and_the_jsonl_still_indexed(
    tmp_path, monkeypatch, capsys
):
    mcp = FakeMCP(
        {
            "session_enter": _entered(),
            "handoff_write_v4": {"error": "not_party_to_dispatch"},
        }
    )
    entry = _seat.enter(mcp, app_id="hanuman", session_id="s-1", dispatch_id="PKT00001")
    indexed = []
    monkeypatch.setattr(mcp_client, "shutdown", lambda *a, **kw: True)
    monkeypatch.setattr(crown, "index_session", lambda **kw: indexed.append(kw))
    state = _state(tmp_path, monkeypatch, seat=entry, mcp_call=mcp)
    _shutdown(state)
    assert indexed, "the transcript is indexed whatever the broker said"
    rows = [e["receipt"] for e in state.writer.read_entries() if e["type"] == "receipt"]
    assert rows[-1]["event"] == "seat_closed"
    assert rows[-1]["error"] == "not_party_to_dispatch"
    assert "seat close FAILED" in capsys.readouterr().out


def test_shutdown_without_a_seat_makes_no_closeout_call(tmp_path, monkeypatch):
    mcp = FakeMCP()
    monkeypatch.setattr(mcp_client, "shutdown", lambda *a, **kw: True)
    monkeypatch.setattr(crown, "index_session", lambda **kw: None)
    state = _state(tmp_path, monkeypatch, seat=None, mcp_call=mcp)
    _shutdown(state)
    assert mcp.calls == []


# -- main() ----------------------------------------------------------------


@pytest.fixture
def fake_transport(tmp_path, monkeypatch):
    """willow-mcp over stdio, faked: start/call/shutdown recorded."""
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)
    monkeypatch.delenv("WILLOW_APP_ID", raising=False)
    monkeypatch.delenv("RATATOSK_APP_ID", raising=False)
    monkeypatch.delenv("WILLOW_AGENT_NAME", raising=False)
    mcp = FakeMCP({"session_enter": _entered(), "handoff_write_v4": {"ok": True}})
    torn = {"down": 0}

    def shutdown_(*a, **kw):
        torn["down"] += 1
        return True

    monkeypatch.setattr(mcp_client, "start", lambda: ([], set()))
    monkeypatch.setattr(mcp_client, "call", mcp)
    monkeypatch.setattr(mcp_client, "shutdown", shutdown_)
    monkeypatch.setattr(crown, "index_session", lambda **kw: None)
    monkeypatch.setattr("builtins.input", lambda *_a: "/exit")
    mcp.torn = torn
    return mcp


def test_app_id_without_mcp_is_refused_at_the_prompt(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["ratatosk", "--app-id", "hanuman"])
    with pytest.raises(SystemExit) as info:
        crown.main()
    assert info.value.code == 2
    assert "--app-id requires --mcp" in capsys.readouterr().err


def test_dispatch_id_without_app_id_is_refused(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["ratatosk", "--mcp", "--dispatch-id", "PKT"])
    with pytest.raises(SystemExit) as info:
        crown.main()
    assert info.value.code == 2
    assert "--dispatch-id requires --app-id" in capsys.readouterr().err


def test_the_orchestrator_seat_is_refused_from_the_flag(
    fake_transport, monkeypatch, capsys
):
    monkeypatch.setattr("sys.argv", ["ratatosk", "--mcp", "--app-id", "willow"])
    with pytest.raises(SystemExit) as info:
        crown.main()
    assert info.value.code == 2
    assert "human orchestrator" in capsys.readouterr().err
    assert fake_transport.calls == [], "refused before the transport was even started"


def test_main_enters_once_with_the_writers_session_id_and_sets_the_env(
    fake_transport, monkeypatch, capsys
):
    seen_env = {}

    def start_():
        seen_env.update(
            {
                k: os.environ.get(k)
                for k in ("WILLOW_APP_ID", "RATATOSK_APP_ID", "WILLOW_AGENT_NAME")
            }
        )
        return [], set()

    monkeypatch.setattr(mcp_client, "start", start_)
    monkeypatch.setattr(
        "sys.argv",
        [
            "ratatosk",
            "--mcp",
            "--local",
            "--app-id",
            "hanuman",
            "--dispatch-id",
            "PKT00001",
        ],
    )
    crown.main()
    (enter,) = fake_transport.named("session_enter")
    assert enter["app_id"] == "hanuman" and enter["dispatch_id"] == "PKT00001"
    assert len(enter["session_id"]) == 36, "the JSONL's uuid, not a made-up id"
    assert seen_env == {
        "WILLOW_APP_ID": "hanuman",
        "RATATOSK_APP_ID": "hanuman",
        "WILLOW_AGENT_NAME": "hanuman",
    }, "the flag sets the env before the willow-mcp child is spawned"
    out = capsys.readouterr().out
    assert "seat entered app=hanuman" in out
    assert "[seat: hanuman/dispatch]" in out
    # Closed as the seat on the way out, and the packet was never "accepted".
    assert fake_transport.named("handoff_write_v4")
    assert "dispatch_accept" not in [n for n, _ in fake_transport.calls]


def test_an_error_from_session_enter_exits_2_before_any_turn(
    fake_transport, monkeypatch, capsys
):
    fake_transport.answers["session_enter"] = {"error": "gate denied"}
    turns = []
    monkeypatch.setattr(crown, "_run_turn", lambda s, u: turns.append(u))
    monkeypatch.setattr("builtins.input", lambda *_a: "hello")
    monkeypatch.setattr(
        "sys.argv", ["ratatosk", "--mcp", "--local", "--app-id", "hanuman"]
    )
    with pytest.raises(SystemExit) as info:
        crown.main()
    assert info.value.code == 2
    assert turns == []
    assert "gate denied" in capsys.readouterr().out
    assert fake_transport.torn["down"] == 1, "the transport is torn down on refusal"


def test_blockers_are_printed_and_exit_2(fake_transport, monkeypatch, capsys):
    fake_transport.answers["session_enter"] = _entered(
        blockers={
            "count": 1,
            "items": [{"id": "no_egress_lease", "summary": "no lease"}],
        }
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "ratatosk",
            "--mcp",
            "--local",
            "--app-id",
            "hanuman",
            "--dispatch-id",
            "PKT00001",
        ],
    )
    with pytest.raises(SystemExit) as info:
        crown.main()
    assert info.value.code == 2
    assert "no_egress_lease" in capsys.readouterr().out


def test_the_persona_leads_the_system_prompt_in_main(fake_transport, monkeypatch):
    captured = {}
    real_state = crown.RuntimeState

    class Spy(real_state):
        def __init__(self, **kw):
            captured["system_prompt"] = kw["system_prompt"]
            super().__init__(**kw)

    monkeypatch.setattr(crown, "RuntimeState", Spy)
    monkeypatch.setattr(
        "sys.argv", ["ratatosk", "--mcp", "--local", "--app-id", "hanuman"]
    )
    crown.main()
    assert captured["system_prompt"].startswith(PERSONA.strip())
    assert "# Build the thing" in captured["system_prompt"]


def test_without_app_id_main_is_the_plain_repl(fake_transport, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["ratatosk", "--mcp", "--local"])
    crown.main()
    assert fake_transport.named("session_enter") == []
    assert fake_transport.named("handoff_write_v4") == []
    assert "[seat:" not in capsys.readouterr().out


def test_listen_with_app_id_enters_as_the_seat_and_the_node_is_the_seat(
    fake_transport, monkeypatch, capsys
):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "hanuman")
    nodes = []

    def run_forever(self, on_status=None):
        nodes.append(self.node)
        raise KeyboardInterrupt

    monkeypatch.setattr("ratatosk.listener.BusListener.run_forever", run_forever)
    monkeypatch.setattr(
        "sys.argv", ["ratatosk", "--mcp", "--listen", "--app-id", "hanuman"]
    )
    # A listener enters without a packet; the broker answers the human shape.
    fake_transport.answers["session_enter"] = _entered(
        entry_mode="human", dispatch_id=None, closeout_tools=["session_handoff_write"]
    )
    fake_transport.answers["session_handoff_write"] = {"path": "/h/listen.md"}
    crown.main()
    (enter,) = fake_transport.named("session_enter")
    assert enter["app_id"] == "hanuman"
    assert enter["session_id"].startswith("hanuman-listen-")
    assert nodes == ["hanuman"], "the listener's node is the seat, not 'ratatosk'"
    out = capsys.readouterr().out
    assert "seat entered app=hanuman" in out
    # Loki 32A3263E finding 1: the listener leaves the way it came — the
    # closeout runs on Ctrl-C, before the transport goes, and is inked.
    (close,) = fake_transport.named("session_handoff_write")
    assert close["app_id"] == "hanuman"
    assert close["session_id"] == enter["session_id"]
    assert "seat closed app=hanuman" in out and "handoff=/h/listen.md" in out
    names = [n for n, _ in fake_transport.calls]
    assert names.index("session_handoff_write") > names.index("session_enter")
    assert fake_transport.torn["down"] == 1


def test_listen_closes_the_seat_even_when_the_channel_is_refused(
    fake_transport, monkeypatch, capsys
):
    """The seat entered before BusListener refused the unset channel; the
    refusal path must still close it (and still exit 1, as before)."""
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    fake_transport.answers["session_enter"] = _entered(
        entry_mode="human", dispatch_id=None, closeout_tools=["session_handoff_write"]
    )
    monkeypatch.setattr(
        "sys.argv", ["ratatosk", "--mcp", "--listen", "--app-id", "hanuman"]
    )
    with pytest.raises(SystemExit) as info:
        crown.main()
    assert info.value.code == 1
    assert fake_transport.named("session_handoff_write")
    assert "seat closed app=hanuman" in capsys.readouterr().out
    assert fake_transport.torn["down"] == 1


def test_listen_closeout_failure_is_inked_and_does_not_block_teardown(
    fake_transport, monkeypatch, capsys
):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "hanuman")
    fake_transport.answers["session_enter"] = _entered(
        entry_mode="human", dispatch_id=None, closeout_tools=["session_handoff_write"]
    )
    fake_transport.answers["session_handoff_write"] = {"error": "postgres_unavailable"}

    def run_forever(self, on_status=None):
        raise KeyboardInterrupt

    monkeypatch.setattr("ratatosk.listener.BusListener.run_forever", run_forever)
    monkeypatch.setattr(
        "sys.argv", ["ratatosk", "--mcp", "--listen", "--app-id", "hanuman"]
    )
    crown.main()
    out = capsys.readouterr().out
    assert "seat close FAILED app=hanuman" in out and "postgres_unavailable" in out
    assert fake_transport.torn["down"] == 1


def test_listen_a_raising_shutdown_is_one_info_line_not_a_traceback(
    fake_transport, monkeypatch, capsys
):
    """Loki 04C311E4: the listen finally guards shutdown() like the REPL's
    _shutdown does — the closeout already ran, and the exit stays clean."""
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "hanuman")
    fake_transport.answers["session_enter"] = _entered(entry_mode="human")
    fake_transport.answers["session_handoff_write"] = {"path": "/h/listen.md"}

    def run_forever(self, on_status=None):
        raise KeyboardInterrupt

    def explode(*a, **kw):
        raise OSError("pipe gone")

    monkeypatch.setattr("ratatosk.listener.BusListener.run_forever", run_forever)
    monkeypatch.setattr(mcp_client, "shutdown", explode)
    monkeypatch.setattr(
        "sys.argv", ["ratatosk", "--mcp", "--listen", "--app-id", "hanuman"]
    )
    crown.main()
    out = capsys.readouterr().out
    assert "seat closed app=hanuman" in out
    assert "[mcp] shutdown failed: pipe gone" in out


def test_listen_with_a_refused_seat_tears_down_and_exits_2(
    fake_transport, monkeypatch, capsys
):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "hanuman")
    fake_transport.answers["session_enter"] = {"error": "gate denied"}
    monkeypatch.setattr(
        "sys.argv", ["ratatosk", "--mcp", "--listen", "--app-id", "hanuman"]
    )
    with pytest.raises(SystemExit) as info:
        crown.main()
    assert info.value.code == 2
    assert fake_transport.torn["down"] == 1
    assert "gate denied" in capsys.readouterr().out


def test_grove_sender_posts_as_the_seat_named_after_import(monkeypatch):
    """`crown --app-id` sets WILLOW_AGENT_NAME after grove was imported; the
    sender must read it at send time, the way it reads the channel."""
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "hanuman")
    monkeypatch.setenv("WILLOW_AGENT_NAME", "hanuman")
    seen = {}

    def call(name, inputs):
        seen.update(inputs)
        return json.dumps({"ok": True})

    grove.make_mcp_sender(call, app_id="hanuman")("hello")
    assert seen["sender"] == "hanuman"
