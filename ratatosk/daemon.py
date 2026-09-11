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
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ratatosk import grove
from ratatosk.listener import BusListener
from ratatosk.paths import ratatosk_data_root
from ratatosk.protocol.envelope import Envelope

logger = logging.getLogger(__name__)

#: How often to post a Grove heartbeat while idle. A wake still gets answered
#: on the next poll_interval tick regardless of where we are in this clock —
#: the heartbeat and the message loop are independent schedules.
DEFAULT_HEARTBEAT_INTERVAL = 30.0

#: Ledger path env override for the seal watcher (mirrors
#: RATATOSK_GROVE_CHANNEL's read-at-call-time convention). Unset means the
#: watcher is not constructed — dark-safe, no ledger to watch by default.
SEAL_LEDGER_ENV = "RATATOSK_SEAL_LEDGER"
SEAL_OFFSET_ENV = "RATATOSK_SEAL_OFFSET"


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


class JsonlTailWatcher:
    """Tails an append-only JSONL ledger and dispatches matching records.

    Generic on purpose: this class knows nothing about Nestor, seals, or
    SOIL. It is handed a ledger path, a predicate over the parsed record
    (``lambda record: record.get("op") == "seal"`` for the seal case), a
    callback, and a path to persist the read offset. Any other JSONL ledger
    watched for any other op is the same class with a different predicate.

    Delivery guarantee — read this before wiring a consumer: the offset is
    persisted PER RECORD, immediately after that record's callback returns
    successfully, not once at the end of a batch. That makes a crash or a
    raising callback resume exactly after the last *successfully processed*
    record — every earlier record in the batch is never re-fired. The one
    remaining gap is the single record whose callback is still running (or
    just returned) when the process dies before the offset write lands on
    disk: that one record can be delivered twice on restart. This is
    therefore an **at-least-once** watcher, not exactly-once, and ``on_seal``
    /``callback`` MUST be idempotent (safe to run twice on the same record).
    Do not build a consumer here that assumes exactly-once delivery.

    A partial trailing line (the writer is still mid-append) is left
    unconsumed; it is read whole on a later poll once its newline lands.

    Fails loud, not silent: a line that does not parse as JSON is logged and
    skipped — its bytes are still consumed (and that consumption persisted)
    so it is not retried forever — and the watch continues. A callback that
    raises on a parse-OK record stops the batch right there: the exception is
    logged with ``exc_info`` and re-raised, the offset is NOT advanced past
    the failing record (every prior record in the batch is already
    committed), and the next poll retries only that record. A permanently
    failing record therefore surfaces on every single poll — via the raised
    exception, which ``SeatDaemon.poll_seal_ledger`` turns into a repeated
    ``on_status``/log line — rather than silently wedging the watcher forever
    with no signal.

    Rotation/truncation-safe: if the ledger's current size is smaller than
    the persisted offset (the file was truncated, or rotated to a fresh file
    at the same path) the offset is reset to 0 and the ledger is re-read from
    the start, rather than seeking past EOF and silently missing everything
    written after rotation. An inode change is treated the same way when it
    can be observed within a single process's lifetime.

    An I/O error reading the ledger is logged with ``exc_info`` and
    re-raised; the caller (``SeatDaemon.poll_seal_ledger``) catches it so one
    bad poll cannot kill the heartbeat, and the next poll retries the read
    from the last persisted offset.
    """

    def __init__(
        self,
        ledger_path: str | Path,
        op_predicate: Callable[[dict], bool],
        callback: Callable[[dict], None],
        offset_store_path: str | Path,
    ):
        self.ledger_path = Path(ledger_path)
        self.op_predicate = op_predicate
        self.callback = callback
        self.offset_store_path = Path(offset_store_path)
        # Best-effort, in-process-only rotation signal: persisted offset vs.
        # ledger size already catches truncation/rotation across restarts;
        # this catches an inode swap observed within one process's lifetime
        # (e.g. logrotate's create-new-file-same-name) without needing to
        # persist the inode across restarts.
        self._last_seen_inode: int | None = None

    def _load_offset(self) -> int:
        try:
            text = self.offset_store_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return 0
        if not text:
            return 0
        try:
            return int(text)
        except ValueError:
            logger.error(
                "seal watcher: corrupt offset store %s, restarting from 0",
                self.offset_store_path,
                exc_info=True,
            )
            return 0

    def _save_offset(self, offset: int) -> None:
        self.offset_store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.offset_store_path.with_name(self.offset_store_path.name + ".tmp")
        tmp.write_text(str(offset), encoding="utf-8")
        tmp.replace(self.offset_store_path)

    def poll(self) -> int:
        """Read whatever new complete lines exist, dispatch the matches.

        Returns the number of records the callback was invoked for. Never
        raises for a missing ledger file (nothing to watch yet) or a
        malformed line (logged and skipped); a genuine read/IO error is
        logged with ``exc_info`` and re-raised so the caller decides whether
        and when to retry.

        The offset is saved after EVERY consumed line, not once at the end —
        see the class docstring for exactly what guarantee that does (and
        does not) buy. A callback that raises stops the loop immediately,
        without saving past the failing record, and re-raises after logging;
        every record processed earlier in this same call is already
        persisted and will not be re-delivered.
        """
        offset = self._load_offset()
        try:
            with open(self.ledger_path, "rb") as handle:
                stat = os.fstat(handle.fileno())
                size = stat.st_size
                inode = stat.st_ino
                rotated = size < offset or (
                    self._last_seen_inode is not None and inode != self._last_seen_inode
                )
                if rotated:
                    logger.warning(
                        "seal watcher: ledger %s appears rotated or truncated "
                        "(size=%d, persisted offset=%d) — resetting to 0",
                        self.ledger_path,
                        size,
                        offset,
                    )
                    offset = 0
                    self._save_offset(0)
                self._last_seen_inode = inode
                handle.seek(offset)
                data = handle.read()
        except FileNotFoundError:
            return 0
        except OSError:
            logger.error(
                "seal watcher: failed reading ledger %s", self.ledger_path, exc_info=True
            )
            raise

        if not data:
            return 0

        pos = offset
        dispatched = 0
        for raw_line in data.splitlines(keepends=True):
            if not raw_line.endswith(b"\n"):
                # Partial trailing line — the writer is still mid-append.
                # Leave it unconsumed; a later poll reads it whole.
                break
            line_len = len(raw_line)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                pos += line_len
                self._save_offset(pos)
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.error(
                    "seal watcher: malformed line in %s, skipping",
                    self.ledger_path,
                    exc_info=True,
                )
                pos += line_len
                self._save_offset(pos)
                continue
            if self.op_predicate(record):
                try:
                    self.callback(record)
                except Exception:
                    logger.error(
                        "seal watcher: on_seal raised for record in %s — "
                        "not advancing past it, will retry next poll",
                        self.ledger_path,
                        exc_info=True,
                    )
                    raise
                dispatched += 1
            pos += line_len
            self._save_offset(pos)
        return dispatched


def default_on_seal(record: dict) -> None:
    """Log-only default seal handler — dark-safe to ship without a consumer.

    The intended consumer of a seal watch is a willow-mcp handler that, on a
    ``seal`` record, upgrades the matching SOIL governance record (keyed by
    ``nestor_pair_id``) to ``status=sealed`` with ``verifier`` + ``seal_sig``
    and enqueues a notice. Building that handler is a SEPARATE willow-mcp
    task — out of scope here. This default only proves the watch fired, so
    an unconfigured daemon says something honest about the seal it saw
    instead of raising or silently dropping it (same shape as
    ``default_activate`` above).
    """
    logger.info("seal observed: %s", record)


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
        seal_ledger_path: str | Path | None = None,
        seal_offset_path: str | Path | None = None,
        on_seal: Callable[[dict], None] | None = None,
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

        # Seal watch is opt-in: no ledger path (explicit or via
        # RATATOSK_SEAL_LEDGER) means no watcher is built at all — a daemon
        # with nothing configured behaves exactly as it did before this
        # feature existed.
        ledger_path = seal_ledger_path or os.environ.get(SEAL_LEDGER_ENV)
        self.seal_watcher: JsonlTailWatcher | None = None
        if ledger_path:
            offset_path = (
                seal_offset_path
                or os.environ.get(SEAL_OFFSET_ENV)
                or (ratatosk_data_root() / "seal_watch.offset")
            )
            self.seal_watcher = JsonlTailWatcher(
                ledger_path=ledger_path,
                op_predicate=lambda record: record.get("op") == "seal",
                callback=on_seal or default_on_seal,
                offset_store_path=offset_path,
            )

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

    def poll_seal_ledger(self, on_status: Callable[[str], None] | None = None) -> int:
        """Poll the seal ledger once, if a watcher is configured.

        Runs on ``poll_interval`` cadence (see ``run_forever``) — the same
        tick as the bus poll — not on the slower ``heartbeat_interval``, so a
        seal is picked up within one poll_interval instead of waiting for the
        next heartbeat. Deliberately isolated with its own try/except: a
        raise out of ``JsonlTailWatcher.poll`` (an IO error, or a raising
        ``on_seal`` callback stuck on one record) is surfaced via logging
        (already done inside ``poll``, with ``exc_info``) and via
        ``on_status`` — on every call, so a permanently-failing record cannot
        wedge silently — but swallowed here so it cannot take the heartbeat
        down with it. The next call retries from the last persisted offset —
        the watch never silently dies.
        """
        if self.seal_watcher is None:
            return 0
        try:
            dispatched = self.seal_watcher.poll()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            if on_status:
                on_status(f"seal watch error: {exc}")
            return 0
        # A heartbeat-style trace on *every* poll, match or not — this is
        # what makes "the ledger has been quiet" distinguishable from "the
        # watcher stopped running": the former keeps logging zero, the
        # latter stops logging at all.
        logger.debug("seal watch poll: %d seal(s) observed", dispatched)
        if on_status and dispatched:
            on_status(f"seal watch: {dispatched} seal(s) observed")
        return dispatched

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
        self.poll_seal_ledger(on_status)
        while not self._stop.is_set():
            try:
                self.listener.run_once()
            except Exception as exc:
                if on_status:
                    on_status(f"poll error: {exc}")
            # Decoupled cadences: the seal ledger is polled every loop tick
            # (poll_interval — the same tick the bus poll already runs on),
            # independent of the heartbeat, which still fires only every
            # heartbeat_interval. Each call is independently guarded
            # (poll_seal_ledger never raises) so a bad ledger read cannot
            # suppress the heartbeat, and a heartbeat failure does not skip
            # the seal poll either — order here does not create a dependency.
            self.poll_seal_ledger(on_status)
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
