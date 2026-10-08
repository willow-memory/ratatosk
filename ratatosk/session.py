"""JSONL session writer and tier-0 deposit bundle.

Every row is stamped (one-script part 3, "record + stamp"): ``who`` (the
gate/persona-verified identity), ``standing`` (always ``unattested`` when the
writer emits it; only a witness or the human moves it, by appending an
``attestation`` row), ``turn``, and ``code_hash`` (the hash of the code that
produced the row). The model never writes ``standing`` or a seal.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ratatosk.paths import ratatosk_data_root

_SESSION_DIR_ENV = "RATATOSK_SESSION_DIR"
VERSION = "ratatosk-1.1"

#: The only standings a row can carry. The writer emits UNATTESTED; the other
#: two are reachable only through ``write_attestation`` (a witness/human act).
STANDING_UNATTESTED = "unattested"
STANDING_WITNESSED = "witnessed"
STANDING_SEALED = "sealed"
STANDINGS = (STANDING_UNATTESTED, STANDING_WITNESSED, STANDING_SEALED)

_WHO_UNKNOWN = "unknown"
_STAMP_KEYS = ("who", "standing", "seal")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: sha256 of this module: the code that produced every row this process writes.
CODE_HASH = _file_hash(Path(__file__))


def _temp_roots() -> tuple[Path, ...]:
    roots = {Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp")}
    roots.add(Path("/dev/shm"))
    return tuple(sorted(roots))


def is_temp_path(path: str | Path) -> bool:
    """True when ``path`` lives under a temp location (a pile pointer there is
    a pointer to something that will vanish)."""
    resolved = Path(path).expanduser().resolve()
    for root in _temp_roots():
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return True
    return False


def _default_session_dir() -> Path:
    slug = str(Path.cwd()).replace("/", "-")
    return ratatosk_data_root() / "sessions" / slug


def session_dir() -> Path:
    env = os.environ.get(_SESSION_DIR_ENV)
    directory = Path(env) if env else _default_session_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class SessionWriter:
    def __init__(self, cwd: str = "", who: str = _WHO_UNKNOWN):
        self.session_id = str(uuid.uuid4())
        self.cwd = cwd or str(Path.cwd())
        self.who = who or _WHO_UNKNOWN
        self.path = session_dir() / f"{self.session_id}.jsonl"
        self._last_uuid: str | None = None
        self._turn: int | None = None

    def _entry(self, etype: str, **extra) -> dict:
        # The stamp is the writer's, never the caller's: a row built from model
        # output cannot carry its own standing, identity or seal.
        forbidden = [key for key in _STAMP_KEYS if key in extra]
        if forbidden:
            raise ValueError(f"row fields {forbidden} are set by the writer only")
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
            "code_hash": CODE_HASH,
            "who": self.who,
            "standing": STANDING_UNATTESTED,
            "turn": self._turn,
        }
        entry.update(extra)
        return entry

    def write(self, entry: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        self._last_uuid = entry["uuid"]

    def write_user(self, text: str) -> None:
        self.write(self._entry("user", message={"role": "user", "content": text}))

    def write_assistant(
        self,
        text: str,
        model: dict | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        """``model`` ({family, version, file_hash}) and ``prompt_hash`` mark a
        model-output row so "same family = one witness" is counted, not
        assumed."""
        extra: dict = {}
        if model is not None:
            extra["model"] = model
        if prompt_hash is not None:
            extra["prompt_hash"] = prompt_hash
        self.write(
            self._entry(
                "assistant", message={"role": "assistant", "content": text}, **extra
            )
        )

    def write_system(self, text: str) -> None:
        self.write(self._entry("system", message={"role": "system", "content": text}))

    def write_tool(self, name: str, tool_use_id: str, result_chars: int) -> None:
        """One tool call, as its own entry type — the name and the size of what
        came back, never the arguments or the result (both can carry secrets
        and both are already in the model's history). The seat closeout
        counts these; before this row existed the transcript did not know a
        tool had been called at all."""
        self.write(
            self._entry(
                "tool", name=name, tool_use_id=tool_use_id, result_chars=result_chars
            )
        )

    def write_receipt(self, receipt: dict) -> None:
        """A model-call receipt (ratatosk.inference.TurnReceipt) as its own
        entry type — provider/model/rung/tokens per call, distinct from the
        conversation so a reader can total them without parsing prose."""
        self.write(self._entry("receipt", receipt=receipt))

    def write_turn_open(self, n: int) -> None:
        """Frame the start of turn ``n``. Rows written until ``turn_close``
        carry ``turn == n``; a crash leaves this row open."""
        self._turn = n
        self.write(self._entry("turn_open", n=n))

    def write_turn_close(self, n: int) -> None:
        self.write(self._entry("turn_close", n=n))
        self._turn = None

    def last_unclosed_turn(self) -> int | None:
        """The last turn opened but never closed, or None. Lets a reader
        hard-close a turn a crash left open."""
        open_turns: list[int] = []
        for row in self.read_entries():
            if row.get("type") == "turn_open":
                open_turns.append(row["n"])
            elif row.get("type") == "turn_close" and row["n"] in open_turns:
                open_turns.remove(row["n"])
        return open_turns[-1] if open_turns else None

    def write_pointer(self, path: str | Path, live: bool = True) -> dict:
        """Record a pointer to a touched file: {path, sha256, mtime, live}.
        Returns the verdict. A temp-location path, or one that cannot be
        read, is a FAILING verdict (``ok`` False) — the row is still written
        so the failure is on the record."""
        target = Path(path).expanduser()
        reason = ""
        sha256: str | None = None
        mtime: float | None = None
        if is_temp_path(target):
            reason = "temp_path"
        try:
            sha256 = _file_hash(target)
            mtime = target.stat().st_mtime
        except OSError:
            reason = reason or "unreadable"
        verdict = {"ok": not reason, "reason": reason}
        self.write(
            self._entry(
                "pointer",
                pointer={
                    "path": str(target),
                    "sha256": sha256,
                    "mtime": mtime,
                    "live": live,
                },
                verdict=verdict,
            )
        )
        return verdict

    def write_attestation(self, target_uuid: str, standing: str, witness: str) -> None:
        """A witness/human act: append a row moving ``target_uuid`` to
        ``witnessed`` or ``sealed``. Append-only — the original row is never
        rewritten. Never call this with model-supplied arguments."""
        if standing not in (STANDING_WITNESSED, STANDING_SEALED):
            raise ValueError(f"cannot attest to standing {standing!r}")
        if not witness:
            raise ValueError("an attestation names its witness")
        self.write(
            self._entry(
                "attestation",
                target=target_uuid,
                attested_standing=standing,
                witness=witness,
            )
        )

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
