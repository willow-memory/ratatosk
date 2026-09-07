from pathlib import Path

from ratatosk.history import index_session, list_sessions, load_session_history, search_sessions
from ratatosk.session import SessionWriter


def test_history_index_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "ratatosk" / "sessions" / "proj"))

    writer = SessionWriter(cwd="/tmp/proj")
    writer.write_user("hello ratatosk")
    writer.write_assistant("hi")
    index_session(
        session_id=writer.session_id,
        cwd=writer.cwd,
        model="claude-test",
        jsonl_path=writer.path,
    )

    rows = list_sessions(limit=5)
    assert rows
    assert rows[0].session_id == writer.session_id

    matches = search_sessions("hello", limit=5)
    assert matches
    assert matches[0].session_id == writer.session_id

    history = load_session_history(writer.session_id)
    assert history[0]["role"] == "user"
    assert "hello ratatosk" in str(history[0]["content"])
