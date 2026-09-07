"""One error shape, an honest schema, signalled truncation."""
import json

from ratatosk.tools import BASH_TOOL, dispatch, problem


def test_problem_states_fault_and_remedy():
    text = problem("It broke.", "Try the other thing.")
    assert "It broke." in text
    assert "Try the other thing." in text


def test_every_failure_is_prose_not_json():
    """Three shapes became one. The reader is a language model."""
    unknown = dispatch("NoSuchTool", {}, set(), None, trusted=True)
    for result in (unknown,):
        try:
            json.loads(result)
        except (json.JSONDecodeError, TypeError):
            continue
        raise AssertionError("failures should not be JSON payloads")


def test_unknown_tool_says_so_plainly():
    result = dispatch("NoSuchTool", {}, set(), None, trusted=True)
    assert "no tool called 'NoSuchTool'" in result
    assert "spelling" in result


def test_missing_file_names_the_remedy(tmp_path):
    result = dispatch("Read", {"file_path": str(tmp_path / "nope.txt")}, set(), None, trusted=True)
    assert "does not exist" in result
    assert "Glob" in result


def test_reading_a_directory_is_distinguished(tmp_path):
    result = dispatch("Read", {"file_path": str(tmp_path)}, set(), None, trusted=True)
    assert "is a directory" in result


def test_read_returns_numbered_lines(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    out = dispatch("Read", {"file_path": str(target)}, set(), None, trusted=True)
    assert "1\talpha" in out
    assert "3\tgamma" in out


def test_read_truncation_is_signalled_with_the_true_total(tmp_path):
    """The defect: it cut at 4000 chars silently, so a partial file looked whole."""
    target = tmp_path / "big.txt"
    target.write_text("x" * 20_000)
    out = dispatch("Read", {"file_path": str(target)}, set(), None, trusted=True)
    assert "[truncated:" in out
    assert "omitted" in out


def test_short_files_are_not_marked(tmp_path):
    target = tmp_path / "small.txt"
    target.write_text("hello\n")
    out = dispatch("Read", {"file_path": str(target)}, set(), None, trusted=True)
    assert "[truncated:" not in out


def test_edit_errors_name_the_remedy(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("aaa")
    missing = dispatch(
        "Edit",
        {"file_path": str(target), "old_string": "zzz", "new_string": "b"},
        set(), None, trusted=True,
    )
    assert "was not found" in missing and "copy the text exactly" in missing

    ambiguous = dispatch(
        "Edit",
        {"file_path": str(target), "old_string": "a", "new_string": "b"},
        set(), None, trusted=True,
    )
    assert "matches 3 times" in ambiguous and "surrounding lines" in ambiguous


def test_bash_schema_does_not_claim_a_shell():
    """It said 'Shell command to execute' while running shell=False."""
    assert "no shell" in BASH_TOOL["description"]
    for absent in ("pipes", "redirects", "globs"):
        assert absent in BASH_TOOL["description"]
    assert "Shell command to execute" not in json.dumps(BASH_TOOL)


def test_shell_in_disguise_is_refused():
    for cmd in ("sh -c 'echo hi'", "bash -c 'echo hi'", "python3 -c 'print(1)'", "perl -e 'print 1'"):
        result = dispatch("Bash", {"command": cmd}, set(), None, trusted=True)
        assert "does not provide one" in result, cmd


def test_a_plain_program_still_runs():
    result = dispatch("Bash", {"command": "echo ratatosk-ok"}, set(), None, trusted=True)
    assert "ratatosk-ok" in result


def test_unbalanced_quote_explains_itself():
    result = dispatch("Bash", {"command": "echo 'unclosed"}, set(), None, trusted=True)
    assert "Could not parse" in result
    assert "unbalanced quote" in result


def test_empty_command_says_what_to_do():
    result = dispatch("Bash", {"command": "   "}, set(), None, trusted=True)
    assert "was empty" in result


def test_timeout_kills_the_whole_process_group(monkeypatch, tmp_path):
    """subprocess.run(timeout=) signals only the direct child; a program that
    spawned its own children left them running after the timeout fired."""
    import ratatosk.tools as tools

    monkeypatch.setattr(tools, "_BASH_TIMEOUT", 2)
    marker = tmp_path / "grandchild-alive.txt"
    script = tmp_path / "spawner.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"( sleep 6; echo alive > {marker} ) &\n"
        "sleep 6\n"
    )
    script.chmod(0o755)

    result = dispatch("Bash", {"command": str(script)}, set(), None, trusted=True)
    assert "timed out" in result
    assert "killed" in result

    import time

    time.sleep(6)
    assert not marker.exists(), "the grandchild outlived the timeout"
