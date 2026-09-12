"""Lightweight trace log for listener receipts."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ratatosk.paths import ratatosk_data_root


def log_trace(trace_id: str, event: str, payload: dict | None = None) -> None:
    trace_path = ratatosk_data_root() / "traces.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "trace_id": trace_id,
        "event": event,
        "payload": payload or {},
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with open(trace_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
