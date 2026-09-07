"""Every verdict goes through one chokepoint."""
import json

import pytest

from ratatosk.capabilities import CapabilityGate
from ratatosk.permission import (
    Decision,
    NeedsConfirmation,
    Verdict,
    check,
    enforce,
)
from ratatosk.policy import PolicyStore
from ratatosk.tools import dispatch, prompt_and_dispatch


def _policy(tmp_path, monkeypatch) -> PolicyStore:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    return PolicyStore()


def test_unknown_tool_defaults_to_confirm_and_does_not_execute(tmp_path, monkeypatch):
    """The defect: `confirm` fell out of the bottom of the if-chain and ran."""
    policy = _policy(tmp_path, monkeypatch)
    assert policy.decide("SomeNewTool") == "confirm"
    with pytest.raises(NeedsConfirmation):
        dispatch("SomeNewTool", {}, set(), None, trusted=False, policy_store=policy)


def test_write_through_exported_dispatch_requires_confirmation(tmp_path, monkeypatch):
    """Any caller of the exported dispatch — not just the REPL — is covered."""
    policy = _policy(tmp_path, monkeypatch)
    target = tmp_path / "should-not-exist.txt"
    with pytest.raises(NeedsConfirmation):
        dispatch(
            "Write",
            {"file_path": str(target), "content": "x"},
            set(),
            None,
            trusted=False,
            policy_store=policy,
        )
    assert not target.exists(), "the tool body must not have run"


def test_edit_through_exported_dispatch_requires_confirmation(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    target = tmp_path / "f.txt"
    target.write_text("before")
    with pytest.raises(NeedsConfirmation):
        dispatch(
            "Edit",
            {"file_path": str(target), "old_string": "before", "new_string": "after"},
            set(),
            None,
            trusted=False,
            policy_store=policy,
        )
    assert target.read_text() == "before"


def test_trust_does_not_override_an_explicit_confirm_rule(tmp_path, monkeypatch):
    """--trust relaxes the unmatched default, not a rule someone wrote down."""
    policy = _policy(tmp_path, monkeypatch)
    policy.set_rule("Write", "confirm")
    decision = check("Write", {}, trusted=True, policy=policy)
    assert decision.verdict is Verdict.CONFIRM
    assert "does not override" in decision.reason


def test_trust_does_relax_the_unmatched_default(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    policy.save([])
    decision = check("SomeNewTool", {}, trusted=True, policy=policy)
    assert decision.verdict is Verdict.ALLOW
    assert decision.source == "trust"


def test_deny_still_returns_an_error_value(tmp_path, monkeypatch):
    """Asymmetric on purpose: a denial is an answer, a confirm is unfinished."""
    policy = _policy(tmp_path, monkeypatch)
    policy.set_rule("Read", "deny")
    result = dispatch("Read", {"file_path": __file__}, set(), None, trusted=True, policy_store=policy)
    assert "error" in json.loads(result)


def test_deny_beats_allow_from_the_other_source(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    policy.set_rule("Bash", "deny")
    assert check("Bash", {"command": "echo hi"}, trusted=True, policy=policy).verdict is Verdict.DENY


def test_an_unreadable_policy_denies_rather_than_opens(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)

    def boom(_name):
        raise OSError("disk gone")

    monkeypatch.setattr(policy, "decide", boom)
    decision = check("Read", {}, trusted=True, policy=policy)
    assert decision.verdict is Verdict.DENY
    assert decision.source == "error"


def test_the_gate_drains(tmp_path, monkeypatch):
    """The module-level gate accumulated a PendingConfirm nothing ever popped."""
    policy = _policy(tmp_path, monkeypatch)
    gate = CapabilityGate()
    for i in range(20):
        check(f"Bash", {"command": f"echo {i}"}, trusted=False, policy=policy, gate=gate)
    assert gate.pending == {}


def test_enforce_raises_on_confirm_and_returns_on_allow(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    policy.set_rule("Read", "allow")
    assert enforce("Read", {}, policy=policy).verdict is Verdict.ALLOW
    policy.set_rule("Read", "confirm")
    with pytest.raises(NeedsConfirmation):
        enforce("Read", {}, policy=policy)


def test_prompt_and_dispatch_asks_then_runs(tmp_path, monkeypatch, capsys):
    policy = _policy(tmp_path, monkeypatch)
    target = tmp_path / "out.txt"
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    result = prompt_and_dispatch(
        "Write",
        {"file_path": str(target), "content": "hello"},
        False,
        set(),
        None,
        policy_store=policy,
    )
    assert target.read_text() == "hello"
    assert "Written" in str(result)
    assert "reason:" in capsys.readouterr().out


def test_prompt_and_dispatch_refusal_does_not_run_the_tool(tmp_path, monkeypatch):
    policy = _policy(tmp_path, monkeypatch)
    target = tmp_path / "out.txt"
    monkeypatch.setattr("builtins.input", lambda _p: "n")
    result = prompt_and_dispatch(
        "Write",
        {"file_path": str(target), "content": "hello"},
        False,
        set(),
        None,
        policy_store=policy,
    )
    assert not target.exists()
    assert json.loads(result)["error"] == "user denied"


def test_decision_is_frozen_and_reports_allowed():
    d = Decision(Verdict.ALLOW, "ok", "policy")
    assert d.allowed
    with pytest.raises(Exception):
        d.verdict = Verdict.DENY
