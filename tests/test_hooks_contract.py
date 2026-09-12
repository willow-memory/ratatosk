"""A hook runs as what it is, and PreTool can block."""

import json
import stat

from ratatosk.hooks import BLOCK_EXIT_CODE, HookRuntime, blocking, merged_input
from ratatosk.tools import dispatch


def _runtime(tmp_path, event, spec) -> HookRuntime:
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"events": {event: [spec]}}))
    return HookRuntime(config_path=cfg)


def _sh(tmp_path, name, body, executable=True):
    script = tmp_path / name
    script.write_text(body)
    if executable:
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_a_shell_hook_with_a_shebang_runs(tmp_path):
    """The defect: every hook was launched as [sys.executable, script]."""
    script = _sh(tmp_path, "hook.sh", "#!/bin/sh\necho shell-hook-ran\n")
    results = _runtime(tmp_path, "PostTool", {"script": str(script)}).run_event(
        "PostTool"
    )
    assert results[0].ok, results[0].output
    assert "shell-hook-ran" in results[0].output


def test_a_non_executable_sh_still_runs_via_extension(tmp_path):
    script = _sh(tmp_path, "hook.sh", "echo via-extension\n", executable=False)
    results = _runtime(tmp_path, "PostTool", {"script": str(script)}).run_event(
        "PostTool"
    )
    assert results[0].ok
    assert "via-extension" in results[0].output


def test_an_unrunnable_hook_refuses_loudly(tmp_path):
    """Silently failing is the behaviour being removed."""
    script = tmp_path / "hook.weird"
    script.write_text("not a program")
    results = _runtime(tmp_path, "PostTool", {"script": str(script)}).run_event(
        "PostTool"
    )
    assert not results[0].ok
    assert "no known interpreter" in results[0].output


def test_an_explicit_interpreter_wins(tmp_path):
    script = tmp_path / "hook.weird"
    script.write_text("echo explicit-interpreter\n")
    results = _runtime(
        tmp_path, "PostTool", {"script": str(script), "interpreter": "/bin/sh"}
    ).run_event("PostTool")
    assert results[0].ok
    assert "explicit-interpreter" in results[0].output


def test_exit_2_blocks_and_the_reason_reaches_the_caller(tmp_path):
    script = _sh(
        tmp_path, "block.sh", f"#!/bin/sh\necho 'no thanks'\nexit {BLOCK_EXIT_CODE}\n"
    )
    results = _runtime(tmp_path, "PreTool", {"script": str(script)}).run_event(
        "PreTool"
    )
    assert results[0].blocked
    assert results[0].decision == "block"
    assert "no thanks" in results[0].reason
    assert results[0].ok, "a deliberate block is not a hook failure"


def test_exit_1_is_a_crash_not_a_block(tmp_path):
    """Conflating them means a hook with a typo silently denies everything."""
    script = _sh(tmp_path, "broken.sh", "#!/bin/sh\nexit 1\n")
    results = _runtime(tmp_path, "PreTool", {"script": str(script)}).run_event(
        "PreTool"
    )
    assert not results[0].ok
    assert results[0].decision == "error"


def test_on_failure_allow_lets_a_crashing_pretool_through(tmp_path):
    script = _sh(tmp_path, "broken.sh", "#!/bin/sh\nexit 1\n")
    results = _runtime(
        tmp_path, "PreTool", {"script": str(script), "on_failure": "allow"}
    ).run_event("PreTool")
    assert not results[0].ok
    assert not results[0].blocked


def test_a_crashing_pretool_denies_by_default(tmp_path):
    script = _sh(tmp_path, "broken.sh", "#!/bin/sh\nexit 1\n")
    results = _runtime(tmp_path, "PreTool", {"script": str(script)}).run_event(
        "PreTool"
    )
    assert results[0].blocked


def test_a_crashing_posttool_does_not_deny(tmp_path):
    script = _sh(tmp_path, "broken.sh", "#!/bin/sh\nexit 1\n")
    results = _runtime(tmp_path, "PostTool", {"script": str(script)}).run_event(
        "PostTool"
    )
    assert not results[0].blocked


def test_a_timeout_is_a_crash(tmp_path):
    script = _sh(tmp_path, "slow.sh", "#!/bin/sh\nsleep 5\n")
    results = _runtime(
        tmp_path, "PreTool", {"script": str(script), "timeout_s": 1}
    ).run_event("PreTool")
    assert not results[0].ok
    assert "timed out" in results[0].output


def test_a_world_writable_config_is_refused(tmp_path):
    script = _sh(tmp_path, "hook.sh", "#!/bin/sh\necho hi\n")
    runtime = _runtime(tmp_path, "PreTool", {"script": str(script)})
    runtime.config_path.chmod(runtime.config_path.stat().st_mode | stat.S_IWOTH)
    results = runtime.run_event("PreTool")
    assert not results[0].ok
    assert "world-writable" in results[0].output


def test_a_world_writable_script_is_refused(tmp_path):
    script = _sh(tmp_path, "hook.sh", "#!/bin/sh\necho hi\n")
    script.chmod(script.stat().st_mode | stat.S_IWOTH)
    results = _runtime(tmp_path, "PreTool", {"script": str(script)}).run_event(
        "PreTool"
    )
    assert not results[0].ok
    assert "world-writable" in results[0].output


def test_group_writable_is_fine(tmp_path):
    """umask 002 is the default here; refusing it would refuse every hook."""
    script = _sh(tmp_path, "hook.sh", "#!/bin/sh\necho hi\n")
    script.chmod(script.stat().st_mode | stat.S_IWGRP)
    results = _runtime(tmp_path, "PostTool", {"script": str(script)}).run_event(
        "PostTool"
    )
    assert results[0].ok


def test_pretool_block_stops_the_tool(tmp_path):
    """The whole point of the name: dispatch used to ignore the exit code."""
    target = tmp_path / "written.txt"
    script = _sh(tmp_path, "block.sh", f"#!/bin/sh\nexit {BLOCK_EXIT_CODE}\n")
    runtime = _runtime(tmp_path, "PreTool", {"script": str(script)})
    result = dispatch(
        "Write",
        {"file_path": str(target), "content": "x"},
        set(),
        None,
        trusted=True,
        hook_runtime=runtime,
    )
    assert not target.exists(), "the tool body must not have run"
    assert "blocked by the hook" in str(result)


def test_posttool_fires_when_the_tool_fails(tmp_path):
    """Every `except` used to return without emitting — holes exactly where a
    hook would want them."""
    marker = tmp_path / "fired.txt"
    script = _sh(
        tmp_path,
        "post.sh",
        f"#!/bin/sh\necho fired > {marker}\n",
    )
    runtime = _runtime(tmp_path, "PostTool", {"script": str(script)})
    result = dispatch(
        "Read",
        {"file_path": str(tmp_path / "does-not-exist")},
        set(),
        None,
        trusted=True,
        hook_runtime=runtime,
    )
    assert "does not exist" in str(result)
    assert marker.exists(), "PostTool must fire on the error path too"


def test_updated_input_only_touches_declared_keys(tmp_path):
    from ratatosk.hooks import HookResult

    results = [
        HookResult(script="s", ok=True, output="", updated_input={"a": 2, "sneaky": 9})
    ]
    assert merged_input(results, {"a": 1}) == {"a": 2}


def test_blocking_finds_the_first_stop():
    from ratatosk.hooks import HookResult

    ok = HookResult(script="a", ok=True, output="")
    stop = HookResult(script="b", ok=True, output="", blocked=True, reason="nope")
    assert blocking([ok, ok]) is None
    assert blocking([ok, stop, ok]) is stop
