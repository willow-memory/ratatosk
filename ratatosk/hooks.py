"""Lifecycle and tool hook runtime."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ratatosk.child_env import child_env
from ratatosk.paths import ratatosk_data_root


@dataclass
class HookResult:
    script: str
    ok: bool
    output: str


class HookRuntime:
    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or (ratatosk_data_root() / "hooks.json")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            return {"events": {}}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {"events": {}}

    def list_events(self) -> dict[str, list[dict]]:
        data = self._load_config()
        events = data.get("events", {})
        return events if isinstance(events, dict) else {}

    def run_event(self, event_name: str, payload: dict | None = None) -> list[HookResult]:
        events = self.list_events()
        specs = events.get(event_name, [])
        results: list[HookResult] = []
        for spec in specs:
            script = str(spec.get("script", "")).strip()
            timeout_s = int(spec.get("timeout_s", 10))
            if not script or not Path(script).exists():
                continue
            stdin = json.dumps(payload or {}).encode("utf-8")
            try:
                proc = subprocess.run(
                    [sys.executable, script],
                    input=stdin,
                    capture_output=True,
                    timeout=timeout_s,
                    check=False,
                    env=child_env(),
                )
                out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").strip()
                results.append(HookResult(script=script, ok=proc.returncode == 0, output=out))
            except Exception as exc:
                results.append(HookResult(script=script, ok=False, output=str(exc)))
        return results
