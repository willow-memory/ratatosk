"""Path helpers — data routes through WILLOW_HOME when present."""
from __future__ import annotations

import os
from pathlib import Path


def ratatosk_data_root() -> Path:
    for key in ("WILLOW_HOME", "WILLOW_STORE_ROOT"):
        value = os.environ.get(key)
        if value:
            return Path(value) / "ratatosk"
    share = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(share) / "ratatosk"
