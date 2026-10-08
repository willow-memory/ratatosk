"""The record + the stamp (one-script part 3): every row stamped, turns framed,
pile pointers verdicted, the model never writing standing."""

from __future__ import annotations

import hashlib
import json
from itertools import pairwise

import pytest

from ratatosk import session as S


@pytest.fixture
def writer(tmp_path, monkeypatch):
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    # pytest's tmp_path is itself under /tmp; the pointer tests that want an
    # "ok" verdict need a world where nothing is a temp location.
    return S.SessionWriter(cwd="/work", who="hanuman")


def _write_every_type(w):
    w.write_user("u")
    w.write_assistant("a", model={"family": "f", "version": "1", "file_hash": "x"})
    w.write_system("s")
    w.write_tool("t", "id1", 3)
    w.write_receipt({"model": "m"})
    w.write_turn_open(1)
    w.write_turn_close(1)


def test_every_row_is_stamped(writer):
    _write_every_type(writer)
    rows = writer.read_entries()
    assert len(rows) == 7
    for row in rows:
        assert row["who"] == "hanuman"
        assert row["standing"] == "unattested"
        assert row["version"] == S.VERSION
        assert row["code_hash"] == S.CODE_HASH
        assert "turn" in row


def test_code_hash_is_hash_of_the_module():
    from pathlib import Path

    expected = hashlib.sha256(Path(S.__file__).read_bytes()).hexdigest()
    assert S.CODE_HASH == expected


def test_uuid_chain_and_types_preserved(writer):
    _write_every_type(writer)
    rows = writer.read_entries()
    assert rows[0]["parentUuid"] is None
    for prev, cur in pairwise(rows):
        assert cur["parentUuid"] == prev["uuid"]
    assert [r["type"] for r in rows[:5]] == [
        "user",
        "assistant",
        "system",
        "tool",
        "receipt",
    ]


def test_model_output_row_carries_model_and_prompt_hash(writer):
    writer.write_assistant(
        "a", model={"family": "f", "version": "1", "file_hash": "x"}, prompt_hash="ph"
    )
    row = writer.read_entries()[0]
    assert row["model"] == {"family": "f", "version": "1", "file_hash": "x"}
    assert row["prompt_hash"] == "ph"


def test_caller_cannot_set_standing_or_seal(writer):
    with pytest.raises(ValueError):
        writer._entry("user", standing="sealed")
    with pytest.raises(ValueError):
        writer._entry("user", seal="sig")
    with pytest.raises(ValueError):
        writer._entry("user", who="someone-else")


def test_default_standing_is_unattested_even_for_attestation_rows(writer):
    writer.write_user("u")
    target = writer.read_entries()[0]["uuid"]
    writer.write_attestation(target, "witnessed", "loki")
    rows = writer.read_entries()
    assert rows[1]["type"] == "attestation"
    assert rows[1]["attested_standing"] == "witnessed"
    assert rows[1]["standing"] == "unattested"
    # append-only: the original row is untouched
    assert rows[0]["standing"] == "unattested"


def test_attestation_refuses_unattested_and_anonymous(writer):
    with pytest.raises(ValueError):
        writer.write_attestation("u", "unattested", "loki")
    with pytest.raises(ValueError):
        writer.write_attestation("u", "witnessed", "")


def test_turn_framing_stamps_inner_rows(writer):
    writer.write_user("before")
    writer.write_turn_open(2)
    writer.write_user("inside")
    writer.write_turn_close(2)
    writer.write_user("after")
    rows = writer.read_entries()
    assert [r["turn"] for r in rows] == [None, 2, 2, 2, None]
    assert rows[1]["type"] == "turn_open" and rows[1]["n"] == 2
    assert rows[3]["type"] == "turn_close" and rows[3]["n"] == 2


def test_unclosed_turn_reported(writer):
    assert writer.last_unclosed_turn() is None
    writer.write_turn_open(1)
    writer.write_turn_close(1)
    assert writer.last_unclosed_turn() is None
    writer.write_turn_open(2)
    assert writer.last_unclosed_turn() == 2
    writer.write_turn_close(2)
    assert writer.last_unclosed_turn() is None


def test_pointer_ok_outside_temp(writer, tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_temp_roots", lambda: ())
    f = tmp_path / "doc.md"
    f.write_text("hello", encoding="utf-8")
    verdict = writer.write_pointer(f)
    assert verdict == {"ok": True, "reason": ""}
    row = writer.read_entries()[0]
    assert row["type"] == "pointer"
    p = row["pointer"]
    assert p["path"] == str(f)
    assert p["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert p["mtime"] == f.stat().st_mtime
    assert p["live"] is True


def test_temp_path_pointer_is_a_failing_verdict(writer, tmp_path):
    f = tmp_path / "scratch.txt"  # tmp_path lives under the temp root
    f.write_text("x", encoding="utf-8")
    assert S.is_temp_path(f)
    verdict = writer.write_pointer(f)
    assert verdict["ok"] is False
    assert verdict["reason"] == "temp_path"
    # the failure is on the record, not swallowed
    assert writer.read_entries()[0]["verdict"]["ok"] is False


def test_missing_file_pointer_fails(writer, tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_temp_roots", lambda: ())
    verdict = writer.write_pointer(tmp_path / "nope")
    assert verdict == {"ok": False, "reason": "unreadable"}


def test_rows_are_valid_json_lines_append_only(writer):
    writer.write_user("one")
    first = writer.path.read_text(encoding="utf-8")
    writer.write_user("two")
    second = writer.path.read_text(encoding="utf-8")
    assert second.startswith(first)
    for line in second.splitlines():
        json.loads(line)
