"""A file-loaded key never enters the environment, and never reaches a child."""

import json
import sys

from ratatosk import crown, redact
from ratatosk.child_env import child_env
from ratatosk.hooks import HookRuntime
from ratatosk.tools import dispatch

FAKE_KEY = "sk-ant-" + "A" * 40


def test_load_api_key_does_not_write_to_the_environment(tmp_path, monkeypatch):
    """The defect: this wrote the key into os.environ, so every child got it."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    creds = tmp_path / ".ratatosk" / "credentials.json"
    creds.parent.mkdir(parents=True)
    creds.write_text(json.dumps({"ANTHROPIC_API_KEY": FAKE_KEY}))
    monkeypatch.setattr(crown, "_HOME", tmp_path)

    assert crown._load_api_key() == FAKE_KEY
    assert "ANTHROPIC_API_KEY" not in __import__("os").environ


def test_credential_source_reports_provenance_never_the_value(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(crown, "_HOME", tmp_path)
    assert crown.credential_source() == "missing"

    creds = tmp_path / ".ratatosk" / "credentials.json"
    creds.parent.mkdir(parents=True)
    creds.write_text(json.dumps({"ANTHROPIC_API_KEY": FAKE_KEY}))
    source = crown.credential_source()
    assert source.startswith("file:")
    assert FAKE_KEY not in source

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    assert crown.credential_source() == "env:ANTHROPIC_API_KEY"


def test_child_env_drops_secrets_and_keeps_placement(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "b" * 36)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("WILLOW_HOME", "/tmp/willow")
    env = child_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["WILLOW_HOME"] == "/tmp/willow"


def test_child_env_keeps_the_termux_essentials(monkeypatch):
    """Without PREFIX and LD_LIBRARY_PATH every child on the phone fails to start."""
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/data/data/com.termux/files/usr/lib")
    env = child_env()
    assert env["PREFIX"].endswith("/usr")
    assert "LD_LIBRARY_PATH" in env


def test_child_env_drops_loader_injection(monkeypatch):
    monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
    assert "LD_PRELOAD" not in child_env()


def test_child_env_is_an_allowlist_not_a_denylist(monkeypatch):
    """A future provider key is excluded by default, not by remembering a rule."""
    monkeypatch.setenv("SOME_FUTURE_PROVIDER_CREDENTIALS", "hunter2")
    assert "SOME_FUTURE_PROVIDER_CREDENTIALS" not in child_env()


def test_bash_tool_cannot_print_the_key(monkeypatch):
    """End to end: the model runs `env` under --trust and sees nothing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    out = dispatch(
        "Bash",
        {"command": f"{sys.executable} -c 'import os;print(dict(os.environ))'"},
        set(),
        None,
        trusted=True,
    )
    assert FAKE_KEY not in out
    assert "ANTHROPIC_API_KEY" not in out


def test_hook_subprocess_cannot_print_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    script = tmp_path / "hook.py"
    script.write_text("import os,sys;sys.stderr.write(str(dict(os.environ)))")
    cfg = tmp_path / "hooks.json"
    cfg.write_text(
        json.dumps({"events": {"PreTool": [{"script": str(script), "timeout_s": 10}]}})
    )
    runtime = HookRuntime(config_path=cfg)
    results = runtime.run_event("PreTool", {"tool_name": "Bash"})
    assert results, "the hook should have run"
    assert FAKE_KEY not in results[0].output


def test_redact_masks_a_provider_key():
    assert FAKE_KEY not in redact.redact(f"key is {FAKE_KEY} ok")
    assert "[REDACTED:provider_api_key]" in redact.redact(FAKE_KEY)


def test_redact_claims_a_pem_block_whole():
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    assert redact.redact(pem) == "[REDACTED:private_key]"


def test_redact_leaves_ordinary_text_alone():
    assert redact.redact("nothing secret here") == "nothing secret here"


def test_found_names_kinds_without_the_value():
    assert redact.found(FAKE_KEY) == ["provider_api_key"]
    assert redact.found("plain") == []


def test_read_tool_redacts_a_key_in_a_file(tmp_path):
    target = tmp_path / "leaky.txt"
    target.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    out = dispatch("Read", {"file_path": str(target)}, set(), None, trusted=True)
    assert FAKE_KEY not in out
