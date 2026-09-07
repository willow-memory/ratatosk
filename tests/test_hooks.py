import json
from pathlib import Path

from ratatosk.hooks import HookRuntime


def test_hook_runtime_runs_script(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    script = tmp_path / "hook.py"
    script.write_text(
        "import json,sys\n"
        "payload=json.loads(sys.stdin.read() or '{}')\n"
        "print(payload.get('event','none'))\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "ratatosk" / "hooks.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"events": {"SessionStart": [{"script": str(script), "timeout_s": 3}]}}),
        encoding="utf-8",
    )
    runtime = HookRuntime(config_path=cfg)
    results = runtime.run_event("SessionStart", {"event": "start"})
    assert len(results) == 1
    assert results[0].ok is True
    assert "start" in results[0].output
