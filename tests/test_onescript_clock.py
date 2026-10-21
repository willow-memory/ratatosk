"""ratatosk's envelope reads the one script's clock (flatten-2 F8; the
operator: "Using the onescript at every path."). With ``ONESCRIPT_NOW`` pinned,
``build_envelope`` stamps ``expires_at`` as pin + ttl in the named ``seconds_z``
format (``2026-10-20T12:00:00Z``), the bytes ``strftime("%Y-%m-%dT%H:%M:%SZ")``
wrote before. With no one script reachable it raises ``Unreachable``.

Locally, with no willow-bot beside, these skip and say so; in CI (``CI`` is
set) they fail instead, because the CI checks willow-bot out for this.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ratatosk import onescript_reach
from ratatosk.protocol import envelope

PIN = "2026-10-20T12:00:00.123456Z"


@pytest.fixture(autouse=True)
def _one_script_or_say_why(monkeypatch):
    home = onescript_reach.where()["onescript"]
    if home is None or not (Path(home) / "onescript" / "clock.py").is_file():
        if os.environ.get("CI"):
            pytest.fail(f"CI has no willow-bot with a clock beside: {home}")
        pytest.skip(f"no willow-bot beside (set ONESCRIPT_HOME): {home}")
    monkeypatch.setenv("ONESCRIPT_NOW", PIN)


def test_utcnow_is_the_pinned_instant():
    assert envelope._utcnow() == datetime(
        2026, 10, 20, 12, 0, 0, 123456, tzinfo=timezone.utc
    )


def test_expires_is_pin_plus_ttl_in_seconds_z():
    env = envelope.build_envelope(
        to="ratatosk",
        prompt="hi",
        intent="chat",
        ttl_seconds=300,
        requires_confirm=False,
    )
    assert env.expires_at == "2026-10-20T12:05:00Z"
    old = (
        datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)
        + timedelta(seconds=300)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert env.expires_at == old


def test_no_one_script_is_unreachable_with_why(monkeypatch, tmp_path):
    monkeypatch.setenv("ONESCRIPT_HOME", str(tmp_path / "nowhere"))
    with pytest.raises(onescript_reach.Unreachable, match="ONESCRIPT_HOME"):
        envelope._utcnow()
