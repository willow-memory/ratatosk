import argparse

from ratatosk import session as _session
from ratatosk.crown import CommandRouter, RuntimeState, _shutdown
from ratatosk.history import index_session
from ratatosk.hooks import HookRuntime
from ratatosk.permission import check
from ratatosk.policy import PolicyRule, PolicyStore


def _state(tmp_path, monkeypatch) -> RuntimeState:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv(
        "RATATOSK_SESSION_DIR", str(tmp_path / "ratatosk" / "sessions" / "proj")
    )
    writer = _session.SessionWriter(cwd=str(tmp_path))
    return RuntimeState(
        args=argparse.Namespace(
            local=True, trust=False, mcp=False, listen=False, deposit=False
        ),
        model="test-model",
        writer=writer,
        history=[],
        system_prompt="test",
        all_tools=[],
        mcp_names=set(),
        mcp_call=None,
        client=None,
        policy=PolicyStore(),
        hooks=HookRuntime(),
    )


def test_permissions_command_roundtrip(tmp_path, monkeypatch, capsys):
    state = _state(tmp_path, monkeypatch)
    router = CommandRouter(state)
    assert router.run("/permissions set Read allow") is False
    assert state.policy.decide("Read") == "allow"
    assert router.run("/permissions list") is False
    out = capsys.readouterr().out
    assert "Read" in out


def test_list_marks_a_rule_that_can_never_fire(tmp_path, monkeypatch, capsys):
    """It used to print unreachable rules exactly like live ones."""
    state = _state(tmp_path, monkeypatch)
    state.policy.save([PolicyRule("*", "deny"), PolicyRule("Read", "allow")])
    router = CommandRouter(state)

    assert router.run("/permissions list") is False
    out = capsys.readouterr().out
    assert "unreachable" in out
    assert "never fire" in out


def test_list_says_nothing_extra_when_every_rule_is_live(tmp_path, monkeypatch, capsys):
    state = _state(tmp_path, monkeypatch)
    state.policy.save([PolicyRule("Read", "allow"), PolicyRule("Bash", "confirm")])
    router = CommandRouter(state)

    assert router.run("/permissions list") is False
    out = capsys.readouterr().out
    assert "unreachable" not in out
    assert "never fire" not in out


def test_explain_reports_the_verdict_without_running_anything(
    tmp_path, monkeypatch, capsys
):
    state = _state(tmp_path, monkeypatch)
    state.policy.set_rule("Read", "deny")
    router = CommandRouter(state)

    assert router.run("/permissions explain Read") is False
    out = capsys.readouterr().out
    assert "Read -> deny" in out
    assert "source: policy" in out
    assert "nothing was executed" in out


def test_explain_agrees_with_the_gate_that_actually_decides(
    tmp_path, monkeypatch, capsys
):
    """An explainer that answers a different question than `check` is worse
    than none — the operator would tune rules against a fiction."""
    state = _state(tmp_path, monkeypatch)
    state.policy.set_rule("Write", "confirm")
    router = CommandRouter(state)

    router.run("/permissions explain Write")
    explained = capsys.readouterr().out

    decision = check("Write", {}, trusted=False, policy=state.policy)
    assert f"Write -> {decision.verdict.value}" in explained
    assert decision.reason in explained


def test_a_scoped_rule_with_spaces_can_actually_be_typed(tmp_path, monkeypatch, capsys):
    """`set` used to require exactly three tokens, so `Bash(git status*)` —
    the whole point of scoping — could not be entered at all."""
    state = _state(tmp_path, monkeypatch)
    router = CommandRouter(state)

    assert router.run("/permissions set Bash(git status*) allow") is False
    assert state.policy.decide("Bash", {"command": "git status --short"}) == "allow"
    assert state.policy.decide("Bash", {"command": "rm -rf ~"}) == "confirm"

    assert router.run("/permissions remove Bash(git status*)") is False
    assert "removed" in capsys.readouterr().out


def test_a_bad_action_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    state = _state(tmp_path, monkeypatch)
    router = CommandRouter(state)

    assert router.run("/permissions set Read banana") is False
    assert "action must be one of" in capsys.readouterr().out


def test_explain_reads_a_path_through_the_scope(tmp_path, monkeypatch, capsys):
    state = _state(tmp_path, monkeypatch)
    state.policy.set_rule("Write(/etc/*)", "deny")
    router = CommandRouter(state)

    router.run("/permissions explain Write /etc/passwd")
    assert "Write -> deny" in capsys.readouterr().out

    router.run("/permissions explain Write /home/me/notes.md")
    assert "Write -> confirm" in capsys.readouterr().out


def test_explain_shows_the_gate_overruling_a_policy_allow(
    tmp_path, monkeypatch, capsys
):
    """The two vocabularies are separate and most-restrictive wins. A scoped
    `allow` on the policy side does not buy past the capability gate, and the
    explainer has to show which source actually decided."""
    state = _state(tmp_path, monkeypatch)
    state.policy.set_rule("Bash(git status*)", "allow")
    router = CommandRouter(state)

    router.run("/permissions explain Bash git status --short")
    out = capsys.readouterr().out
    assert "Bash -> confirm" in out
    assert "source: gate" in out


def test_explain_notes_when_bash_had_no_command(tmp_path, monkeypatch, capsys):
    state = _state(tmp_path, monkeypatch)
    router = CommandRouter(state)

    router.run("/permissions explain Bash")
    assert "no command given" in capsys.readouterr().out


def test_resume_command_loads_history(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.writer.write_user("first prompt")
    state.writer.write_assistant("first answer")
    index_session(
        session_id=state.writer.session_id,
        cwd=state.writer.cwd,
        model=state.model,
        jsonl_path=state.writer.path,
    )
    fresh_state = _state(tmp_path, monkeypatch)
    router = CommandRouter(fresh_state)
    prefix = state.writer.session_id[:8]
    assert router.run(f"/resume {prefix}") is False
    assert fresh_state.history
    assert "resumed context" in str(fresh_state.history[0]["content"])


def test_an_empty_session_says_nothing_was_written(tmp_path, monkeypatch, capsys):
    """`write` creates the JSONL on first entry, so a session that ends before
    writing anything leaves no file. Close-out used to report a failed index
    and then claim "Session written:" for a path that does not exist."""
    state = _state(tmp_path, monkeypatch)
    assert not state.writer.path.exists(), "nothing has been written yet"

    _shutdown(state)

    out = capsys.readouterr().out
    assert "nothing written, nothing indexed" in out
    assert "Session written:" not in out
    assert "index failed" not in out
    assert not state.writer.path.exists(), (
        "must not touch a file to make the sentence true"
    )


def test_a_session_with_entries_still_reports_the_path(tmp_path, monkeypatch, capsys):
    state = _state(tmp_path, monkeypatch)
    state.writer.write_user("hello")

    _shutdown(state)

    out = capsys.readouterr().out
    assert f"Session written: {state.writer.path}" in out
    assert "nothing written" not in out


def test_an_empty_session_with_deposit_says_there_is_no_deposit(
    tmp_path, monkeypatch, capsys
):
    state = _state(tmp_path, monkeypatch)
    state.args.deposit = True

    _shutdown(state)

    out = capsys.readouterr().out
    assert "no deposit" in out
    assert "[deposit]" not in out, "it must not claim to have written one"
