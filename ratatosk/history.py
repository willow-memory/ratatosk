"""Session history index and resume helpers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ratatosk.paths import ratatosk_data_root


@dataclass
class SessionRecord:
    session_id: str
    cwd: str
    model: str
    turns: int
    started_at: str
    ended_at: str
    jsonl_path: str
    first_prompt: str
    resumed_from: str | None = None


def history_db_path() -> Path:
    root = ratatosk_data_root() / "history"
    root.mkdir(parents=True, exist_ok=True)
    return root / "index.db"


def ensure_history_db(db_path: Path | None = None) -> Path:
    path = db_path or history_db_path()
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS sessions (
              session_id TEXT PRIMARY KEY,
              cwd TEXT NOT NULL,
              model TEXT NOT NULL,
              turns INTEGER NOT NULL,
              started_at TEXT NOT NULL,
              ended_at TEXT NOT NULL,
              jsonl_path TEXT NOT NULL,
              first_prompt TEXT NOT NULL DEFAULT '',
              resumed_from TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_ended_at
              ON sessions(ended_at DESC);
            CREATE INDEX IF NOT EXISTS idx_sessions_prompt
              ON sessions(first_prompt);
            """
        )
        con.commit()
    finally:
        con.close()
    return path


def _extract_session_metadata(jsonl_path: Path) -> tuple[str, str, int, str]:
    started = ""
    ended = ""
    turns = 0
    first_prompt = ""
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = str(row.get("timestamp", ""))
        if not started and timestamp:
            started = timestamp
        if timestamp:
            ended = timestamp
        if row.get("type") == "user":
            turns += 1
            if not first_prompt:
                content = row.get("message", {}).get("content", "")
                if isinstance(content, str):
                    first_prompt = (content.strip().splitlines() or [""])[0][:120]
    return started, ended, turns, first_prompt


def index_session(
    *,
    session_id: str,
    cwd: str,
    model: str,
    jsonl_path: Path,
    resumed_from: str | None = None,
) -> None:
    ensure_history_db()
    started, ended, turns, first_prompt = _extract_session_metadata(jsonl_path)
    con = sqlite3.connect(history_db_path())
    try:
        con.execute(
            """
            INSERT INTO sessions
              (session_id, cwd, model, turns, started_at, ended_at, jsonl_path, first_prompt, resumed_from)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
              cwd=excluded.cwd,
              model=excluded.model,
              turns=excluded.turns,
              started_at=excluded.started_at,
              ended_at=excluded.ended_at,
              jsonl_path=excluded.jsonl_path,
              first_prompt=excluded.first_prompt,
              resumed_from=excluded.resumed_from
            """,
            (
                session_id,
                cwd,
                model,
                turns,
                started,
                ended,
                str(jsonl_path),
                first_prompt,
                resumed_from,
            ),
        )
        con.commit()
    finally:
        con.close()


def list_sessions(limit: int = 20) -> list[SessionRecord]:
    ensure_history_db()
    con = sqlite3.connect(history_db_path())
    try:
        rows = con.execute(
            """
            SELECT session_id, cwd, model, turns, started_at, ended_at, jsonl_path, first_prompt, resumed_from
            FROM sessions
            ORDER BY ended_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        con.close()
    return [SessionRecord(*row) for row in rows]


def search_sessions(query: str, limit: int = 20) -> list[SessionRecord]:
    ensure_history_db()
    needle = f"%{query}%"
    con = sqlite3.connect(history_db_path())
    try:
        rows = con.execute(
            """
            SELECT session_id, cwd, model, turns, started_at, ended_at, jsonl_path, first_prompt, resumed_from
            FROM sessions
            WHERE session_id LIKE ? OR cwd LIKE ? OR first_prompt LIKE ?
            ORDER BY ended_at DESC
            LIMIT ?
            """,
            (needle, needle, needle, limit),
        ).fetchall()
    finally:
        con.close()
    return [SessionRecord(*row) for row in rows]


def load_session_history(session_id: str) -> list[dict]:
    ensure_history_db()
    con = sqlite3.connect(history_db_path())
    try:
        row = con.execute(
            "SELECT jsonl_path FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return []
    path = Path(row[0])
    if not path.exists():
        return []
    messages: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = entry.get("message", {})
        role = payload.get("role")
        content = payload.get("content")
        if role in {"user", "assistant"} and content is not None:
            messages.append({"role": role, "content": content})
    return messages
