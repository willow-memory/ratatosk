import json

import pytest

from ratatosk.hooks import HookRuntime
from ratatosk.policy import PolicyStore
from ratatosk.permission import NeedsConfirmation
from ratatosk.tools import dispatch


def test_bash_blocked_without_trust():
    """Now raises rather than returning an error string.

    A confirm is an unfinished decision, not an answer — a caller that does not
    handle it must fail closed. This previously returned a JSON error only
    because the gate happened to refuse; the `confirm` verdict itself fell
    through and executed.
    """
    with pytest.raises(NeedsConfirmation):
        dispatch("Bash", {"command": "echo hi"}, set(), None, trusted=False)


def test_bash_allowed_with_trust():
    result = dispatch("Bash", {"command": "echo ratatosk-test"}, set(), None, trusted=True)
    assert "ratatosk-test" in str(result)


def test_policy_can_deny_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    policy = PolicyStore()
    policy.set_rule("Read", "deny")
    result = dispatch("Read", {"file_path": __file__}, set(), None, trusted=True, policy_store=policy)
    payload = json.loads(result)
    assert "error" in payload


def test_hook_runtime_post_tool(tmp_path):
    hook_script = tmp_path / "hook.py"
    hook_script.write_text("print('post-tool-called')\n", encoding="utf-8")
    hook_cfg = tmp_path / "hooks.json"
    hook_cfg.write_text(
        json.dumps({"events": {"PostTool": [{"script": str(hook_script), "timeout_s": 3}]}}),
        encoding="utf-8",
    )
    runtime = HookRuntime(config_path=hook_cfg)
    result = dispatch(
        "Read",
        {"file_path": __file__},
        set(),
        None,
        trusted=True,
        hook_runtime=runtime,
    )
    assert "test_hook_runtime_post_tool" in str(result)
