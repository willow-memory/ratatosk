"""JsonlTailWatcher — the generic append-only-JSONL tail watcher that backs
the daemon's seal watch.

Kept schema-agnostic in the tests too: the predicate here matches
``op == "seal"`` because that is the configured Nestor case, but the class
under test never assumes that shape.
"""
import json
import logging

import pytest

from ratatosk.daemon import JsonlTailWatcher


def _append(path, *records):
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _is_seal(record: dict) -> bool:
    return record.get("op") == "seal"


def test_new_seal_fires_the_callback_once(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    _append(ledger, {"op": "propose", "id": 1}, {"op": "seal", "id": 2, "nestor_pair_id": "p2"})

    seen = []
    watcher = JsonlTailWatcher(ledger, _is_seal, seen.append, offset)

    dispatched = watcher.poll()

    assert dispatched == 1
    assert seen == [{"op": "seal", "id": 2, "nestor_pair_id": "p2"}]


def test_a_second_poll_with_no_new_lines_does_not_refire(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    _append(ledger, {"op": "seal", "id": 1})

    seen = []
    watcher = JsonlTailWatcher(ledger, _is_seal, seen.append, offset)
    watcher.poll()
    watcher.poll()

    assert len(seen) == 1


def test_restart_reads_the_persisted_offset_and_does_not_refire(tmp_path):
    """A fresh JsonlTailWatcher instance against the same offset file stands
    in for a process restart — the offset file, not the object, is what a
    real restart carries forward."""
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    _append(ledger, {"op": "seal", "id": 1})

    seen_first = []
    JsonlTailWatcher(ledger, _is_seal, seen_first.append, offset).poll()
    assert seen_first == [{"op": "seal", "id": 1}]

    # New instance, same files — the restart.
    seen_second = []
    dispatched = JsonlTailWatcher(ledger, _is_seal, seen_second.append, offset).poll()

    assert dispatched == 0
    assert seen_second == []

    # A seal appended *after* the restart still fires normally.
    _append(ledger, {"op": "seal", "id": 2})
    dispatched_after = JsonlTailWatcher(ledger, _is_seal, seen_second.append, offset).poll()
    assert dispatched_after == 1
    assert seen_second == [{"op": "seal", "id": 2}]


def test_a_partial_trailing_line_is_not_consumed_until_complete(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    ledger.write_bytes(b'{"op": "seal", "id": 1}\n{"op": "seal", "id": 2')  # no closing newline

    seen = []
    watcher = JsonlTailWatcher(ledger, _is_seal, seen.append, offset)
    dispatched = watcher.poll()

    assert dispatched == 1
    assert seen == [{"op": "seal", "id": 1}]

    # Completing the trailing line makes it available on the next poll.
    with open(ledger, "ab") as handle:
        handle.write(b'}\n')
    dispatched_after = watcher.poll()
    assert dispatched_after == 1
    assert seen[-1] == {"op": "seal", "id": 2}


def test_malformed_line_is_logged_and_skipped_next_good_line_still_fires(tmp_path, caplog):
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    ledger.write_text('{"op": "seal", "id": 1}\nnot json at all\n{"op": "seal", "id": 2}\n')

    seen = []
    watcher = JsonlTailWatcher(ledger, _is_seal, seen.append, offset)

    with caplog.at_level(logging.ERROR):
        dispatched = watcher.poll()

    assert dispatched == 2
    assert [r["id"] for r in seen] == [1, 2]
    assert any("malformed" in rec.message for rec in caplog.records)

    # And the bad line is not retried forever — a second poll with nothing
    # new dispatches nothing more and does not re-log the same line.
    caplog.clear()
    assert watcher.poll() == 0


def test_io_error_is_surfaced_with_exc_info_and_the_watch_recovers(tmp_path, caplog, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    _append(ledger, {"op": "seal", "id": 1})

    seen = []
    watcher = JsonlTailWatcher(ledger, _is_seal, seen.append, offset)

    real_open = open

    def _boom(path, mode="r", *a, **kw):
        if path == str(ledger) or path == ledger:
            raise OSError("disk went away")
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr("builtins.open", _boom)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(OSError):
            watcher.poll()
    assert any(rec.exc_info for rec in caplog.records)

    monkeypatch.setattr("builtins.open", real_open)
    dispatched = watcher.poll()
    assert dispatched == 1
    assert seen == [{"op": "seal", "id": 1}]


def test_crash_mid_batch_resumes_after_last_processed_record(tmp_path):
    """A per-record offset save means a crash partway through a multi-record
    batch does not re-fire the records already dispatched before the crash —
    only what came after the last persisted offset is seen again."""
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    _append(
        ledger,
        {"op": "seal", "id": 1},
        {"op": "seal", "id": 2},
        {"op": "seal", "id": 3},
    )

    seen = []

    def crash_on_third(record):
        if record["id"] == 3:
            # Stand in for the process dying mid-batch — an unhandled
            # exception is indistinguishable from a hard crash from the
            # offset store's point of view.
            raise RuntimeError("simulated crash")
        seen.append(record)

    watcher = JsonlTailWatcher(ledger, _is_seal, crash_on_third, offset)
    with pytest.raises(RuntimeError):
        watcher.poll()

    assert seen == [{"op": "seal", "id": 1}, {"op": "seal", "id": 2}]

    # Restart: fresh watcher instance, same offset file, a callback that no
    # longer crashes. Only the record that never completed should re-fire.
    seen_after_restart = []
    dispatched = JsonlTailWatcher(
        ledger, _is_seal, seen_after_restart.append, offset
    ).poll()

    assert dispatched == 1
    assert seen_after_restart == [{"op": "seal", "id": 3}]


def test_raising_consumer_does_not_refire_prior_records_and_retries_only_failing_one(
    tmp_path, caplog
):
    """A permanently-failing record must not wedge the batch or re-fire
    records already committed before it — it should surface on every poll
    (via the re-raised exception) and be the only thing retried."""
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    _append(
        ledger,
        {"op": "seal", "id": 1},
        {"op": "seal", "id": 2},
    )

    seen = []

    def always_fails_on_two(record):
        if record["id"] == 2:
            raise ValueError("permanently broken consumer")
        seen.append(record)

    watcher = JsonlTailWatcher(ledger, _is_seal, always_fails_on_two, offset)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError):
            watcher.poll()
    assert seen == [{"op": "seal", "id": 1}]
    assert any(rec.exc_info for rec in caplog.records)

    # Retrying does not re-fire record 1, and fails again on record 2 —
    # every single poll, not just the first, so it cannot silently wedge.
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError):
            watcher.poll()
    assert seen == [{"op": "seal", "id": 1}]
    assert any(rec.exc_info for rec in caplog.records)


def test_rotation_or_truncation_resets_offset_and_new_record_is_seen(tmp_path):
    """If the ledger shrinks below the persisted offset (truncated in place,
    or rotated to a fresh empty file at the same path) the watcher must not
    seek past EOF and silently miss everything written after — it should
    detect the shrink and re-read from 0."""
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"
    _append(ledger, {"op": "seal", "id": 1}, {"op": "seal", "id": 2})

    seen = []
    watcher = JsonlTailWatcher(ledger, _is_seal, seen.append, offset)
    assert watcher.poll() == 2

    # Simulate rotation: truncate to empty, then write a fresh, shorter
    # ledger whose size is smaller than the previously persisted offset.
    ledger.write_text("")
    _append(ledger, {"op": "seal", "id": 99})

    dispatched = watcher.poll()

    assert dispatched == 1
    assert seen[-1] == {"op": "seal", "id": 99}


def test_op_predicate_is_configurable_not_hardcoded_to_nestor_schema(tmp_path):
    """Ratatosk core stays generic — a caller watching for a totally
    different op on a totally different ledger shape works identically."""
    ledger = tmp_path / "other.jsonl"
    offset = tmp_path / "offset"
    _append(ledger, {"kind": "widget_built", "n": 5}, {"kind": "widget_scrapped", "n": 6})

    seen = []
    watcher = JsonlTailWatcher(
        ledger, lambda r: r.get("kind") == "widget_built", seen.append, offset
    )
    assert watcher.poll() == 1
    assert seen == [{"kind": "widget_built", "n": 5}]
