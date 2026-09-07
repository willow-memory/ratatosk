"""JSONL session writer and tier-0 deposit bundle."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ratatosk.paths import ratatosk_data_root

_SESSION_DIR_ENV = "RATATOSK_SESSION_DIR"
VERSION = "ratatosk-1.1"


def _default_session_dir() -> Path:
    slug = str(Path.cwd()).replace("/", "-")
    return ratatosk_data_root() / "sessions" / slug


def session_dir() -> Path:
    env = os.environ.get(_SESSION_DIR_ENV)
    directory = Path(env) if env else _default_session_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class SessionWriter:
    def __init__(self, cwd: str = ""):
        self.session_id = str(uuid.uuid4())
        self.cwd = cwd or str(Path.cwd())
        self.path = session_dir() / f"{self.session_id}.jsonl"
        self._last_uuid: str | None = None

    def _entry(self, etype: str, **extra) -> dict:
        entry_uuid = str(uuid.uuid4())
        entry = {
            "uuid": entry_uuid,
            "parentUuid": self._last_uuid,
            "type": etype,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "isSidechain": False,
            "sessionId": self.session_id,
            "cwd": self.cwd,
            "version": VERSION,
        }
        entry.update(extra)
        return entry

    def write(self, entry: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        self._last_uuid = entry["uuid"]

    def write_user(self, text: str) -> None:
        self.write(self._entry("user", message={"role": "user", "content": text}))

    def write_assistant(self, text: str) -> None:
        self.write(self._entry("assistant", message={"role": "assistant", "content": text}))

    def write_system(self, text: str) -> None:
        self.write(self._entry("system", message={"role": "system", "content": text}))

    def read_entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        entries: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entries.append(json.loads(line))
        return entries

    def export_deposit(self, out_dir: Path | None = None) -> Path:
        out_dir = out_dir or (ratatosk_data_root() / "sync-out")
        out_dir.mkdir(parents=True, exist_ok=True)
        bundle = out_dir / f"{self.session_id}.deposit.json"
        lines = self.read_entries()
        bundle.write_text(
            json.dumps(
                {
                    "session_id": self.session_id,
                    "cwd": self.cwd,
                    "jsonl_path": str(self.path),
                    "entries": lines,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return bundle
