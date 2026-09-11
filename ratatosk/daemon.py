"""The fleet activation daemon — the process that makes a seat wake itself.

Nothing new is reimplemented here. ``BusListener`` already polls Grove,
validates envelopes, and dispatches to handlers; ``crown.py`` already knows
how to stand up an MCP connection and tear it down cleanly. This module wraps
``BusListener`` with the two things a standing service needs that a foreground
REPL session does not:

- a periodic Grove heartbeat, so ``grove_agents``/``grove_fleet_status`` show
  this seat as live instead of dark between wakes;
- a stoppable run loop (a ``threading.Event`` rather than only
  ``KeyboardInterrupt``), because a process manager stops a service with
  SIGTERM, not Ctrl-C.

``crown.py --mcp --listen`` remains the interactive/tier-0 path (termux boot
uses it directly). This is the systemd-managed sibling: same BusListener,
same envelope protocol, no REPL.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from collections.abc import Callable

from ratatosk import grove
from ratatosk.listener import BusListener
from ratatosk.protocol.envelope import Envelope

#: How often to post a Grove heartbeat while idle. A wake still gets answered
#: on the next poll_interval tick regardless of where we are in this clock —
#: the heartbeat and the message loop are independent schedules.
DEFAULT_HEARTBEAT_INTERVAL = 30.0


def default_activate(env: Envelope) -> str:
    """Fallback WAKE activation when no seat runtime is wired in.

    A real deployment passes its own ``activate`` callable — the small piece
    that hands the packet to crown's command router / hooks runtime — which
    is explicitly out of scope here (that wiring is willow-mcp's
    dispatch-posts-a-wake follow-on). This exists so an unconfigured daemon
    still says something honest about the wake it received, instead of
    raising or silently dropping it.
    """
    return f"[ratatosk] wake trace={env.trace_id} received — no seat runtime wired"


class SeatDaemon:
    """A restart-safe, heartbeating wrapper around ``BusListener``.

    All Grove protocol behavior (channel refusal, envelope validation, own-post
    skipping, the capability gate) lives in ``BusListener`` and is inherited
    unchanged. This class owns only the service-shaped concerns: heartbeat
    scheduling and a stop signal that ``run_forever`` actually respects.
    """

    def __init__(
        self,
        *,
        node: str | None = None,
        channel: str | None = None,
        mcp_call=None,
        activate: Callable[[Envelope], str] | None = None,
        poll_interval: float = 2.0,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    ):
        # Raises ValueError on an unset channel — same refusal as
        # BusListener/crown --listen, inherited rather than duplicated.
        self.listener = BusListener(
            node=node,
            channel=channel,
            mcp_call=mcp_call,
            poll_interval=poll_interval,
            activate=activate or default_activate,
        )
        self.heartbeat_interval = heartbeat_interval
        self._stop = threading.Event()
        self._last_heartbeat = 0.0

    @property
    def node(self) -> str:
        return self.listener.node

    @property
    def channel(self) -> str:
        return self.listener.channel

    def request_stop(self, *_args: object) -> None:
        """Ask ``run_forever`` to exit at the next poll boundary.

        Safe to call from a signal handler (SIGTERM/SIGINT) or from another
        thread — ``threading.Event.set`` is the one thing this needs to be.
        """
        self._stop.set()

    def emit_heartbeat(self) -> bool:
        self._last_heartbeat = time.monotonic()
        return self.listener.emit_heartbeat()

    def _heartbeat_due(self, now: float) -> bool:
        return (now - self._last_heartbeat) >= self.heartbeat_interval

    def run_forever(self, on_status: Callable[[str], None] | None = None) -> None:
        """Run until ``request_stop`` is called (or, pre-set, return at once).

        Restart-safe: this clears nothing that lives past the instance except
        the stop flag itself, so calling it again on the same (or a fresh)
        ``SeatDaemon`` against the same channel resumes cleanly — the cursor
        lives on ``self.listener.state`` and the heartbeat clock on ``self``,
        neither at module scope.
        """
        if on_status:
            on_status(f"listening on {self.channel} as {self.node}")
        self.emit_heartbeat()
        while not self._stop.is_set():
            try:
                self.listener.run_once()
            except Exception as exc:
                if on_status:
                    on_status(f"poll error: {exc}")
            if self._heartbeat_due(time.monotonic()):
                self.emit_heartbeat()
            self._stop.wait(self.listener.poll_interval)
        self._stop.clear()
        if on_status:
            on_status("stopped")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ratatosk-listen",
        description="Run the Grove activation daemon for this seat (systemd-managed sibling of `ratatosk --mcp --listen`)",
    )
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--heartbeat-interval", type=float, default=DEFAULT_HEARTBEAT_INTERVAL)
    args = parser.parse_args(argv)

    from ratatosk import mcp_client

    print("  [mcp] connecting…", flush=True)
    mcp_client.start()
    mcp_call = mcp_client.call
    bound = grove.connect(mcp_call)
    if not bound.ok:
        print(f"  [grove] {bound.detail}", flush=True)

    # Construction inside the try for the same reason crown.py's --listen
    # path does it: BusListener refuses an unset channel, and that refusal
    # arrives after mcp_client.start() has already spawned the server. Built
    # above the try it would traceback out and leave the child running.
    daemon: SeatDaemon | None = None
    refused = ""
    try:
        daemon = SeatDaemon(
            mcp_call=mcp_call,
            poll_interval=args.poll_interval,
            heartbeat_interval=args.heartbeat_interval,
        )
    except ValueError as exc:
        refused = str(exc)

    if refused:
        if not mcp_client.shutdown():
            print("  [mcp] stdio teardown did not finish within timeout", flush=True)
        print(f"  [listen] {refused}", flush=True)
        raise SystemExit(1)

    assert daemon is not None
    signal.signal(signal.SIGTERM, daemon.request_stop)
    signal.signal(signal.SIGINT, daemon.request_stop)

    try:
        daemon.run_forever(on_status=lambda msg: print(f"  [listen] {msg}", flush=True))
    finally:
        if not mcp_client.shutdown():
            print("  [mcp] stdio teardown did not finish within timeout", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
