"""Tier-0 sync deposit — record comes home on adb/USB."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ratatosk.paths import ratatosk_data_root
from ratatosk.session import SessionWriter


def deposit_root() -> Path:
    override = os.environ.get("RATATOSK_DEPOSIT_DIR")
    if override:
        path = Path(override)
    else:
        path = ratatosk_data_root() / "sync-out"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_deposit(writer: SessionWriter) -> Path:
    return writer.export_deposit(deposit_root())


def sync_to_home(home_dir: Path, deposit_path: Path) -> Path:
    home_dir.mkdir(parents=True, exist_ok=True)
    target = home_dir / deposit_path.name
    shutil.copy2(deposit_path, target)
    manifest = home_dir / "manifest.json"
    entries = []
    if manifest.exists():
        entries = json.loads(manifest.read_text(encoding="utf-8")).get("deposits", [])
    entries.append(
        {
            "name": deposit_path.name,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    manifest.write_text(json.dumps({"deposits": entries}, indent=2), encoding="utf-8")
    return target
