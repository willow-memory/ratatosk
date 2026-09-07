# Known Bugs

Sixteen defects were found by reading the runtime end to end (#4). All sixteen
are now fixed. What remains is unimplemented work, listed at the bottom, and it
is not the same thing as a bug.

This file is the map the next session reads. It was wrong for most of a day —
every defect below was fixed across #5–#19 while the list still described them
as open — so it is worth saying plainly: **an entry moves to Fixed in the same
PR that fixes it, or this document lies.**

## Fixed

Each line names the PR that closed it and the test that would catch it coming
back. Kept rather than deleted: the reasoning is why the tests look as they do,
and a defect list with no history reads as though nothing was ever wrong.

### Correctness and safety

- **The listener answered its own messages.** `run_once` filtered on
  `id > cursor` with no sender check, while `parse_grove_message` turned any
  unaddressed text into a chat envelope for this node — an unbounded reply loop
  under `--mcp --listen`, which is what `termux/boot/ratatosk-listen.sh` starts.
  Two guards now, following willow-mcp's `grove_listen.py`: *a seat's own posts
  never wake it*. (#5 — `tests/test_listener.py::test_the_reply_loop_is_closed`)

- **`confirm` was enforced at the call site, not in dispatch.** `PolicyStore`
  yields three verdicts and `dispatch` branched on two, so `confirm` — also the
  default for any unmatched tool — fell through and executed. `ratatosk/permission.py`
  is the single chokepoint now; CONFIRM raises, DENY returns, and `--trust` no
  longer overrides an explicit rule. (#11 — `tests/test_permission.py`)

- **Replay protection failed open on a missing nonce.** The guard read
  `if check_replay and env.nonce:`, so omitting the field disabled the check —
  opt-out by omission. A missing nonce is now an error, and eviction is FIFO via
  an `OrderedDict`: `set.pop()` discarded an arbitrary element, and a discarded
  nonce is one that will be accepted twice. (#20 —
  `tests/test_envelope.py::test_a_missing_nonce_is_rejected_not_skipped`)

- **`_compact` could orphan a `tool_result`.** A blind tail slice could land the
  leading edge on a user message whose matching `tool_use` had just been dropped,
  which the API rejects. `_safe_start` walks the boundary back, a validation pass
  keeps the whole history rather than send an orphan, and the budget now counts
  the system prompt and tool schemas. (#17 — `tests/test_compaction.py`)

- **An interrupt during a turn destroyed the session record.** `KeyboardInterrupt`
  is a `BaseException`, so it escaped `except Exception` and skipped every cleanup
  path — the JSONL survived but was never indexed. Teardown moved into
  `_shutdown(state)` behind a `finally`. (#7 —
  `tests/test_crown_lifecycle.py::test_ctrl_c_mid_turn_still_closes_the_session`)

- **`dispatch()` returned three different error shapes.** A JSON payload, a bare
  `ERROR: {exc}`, and a `[stub]` string — so the model could not tell "I called
  this wrongly" from "the tool broke" from "that tool does not exist". One shape
  now, prose stating fault *and* remedy. (#15 — `tests/test_tool_results.py`)

- **The API key was handed to every subprocess.** `_load_api_key` wrote it into
  `os.environ` and neither `subprocess.run` site passed `env=`, so a model-invoked
  `Bash` running `env` printed it. The environment is a source now, never a
  destination, and `ratatosk/child_env.py` is an allowlist. (#9 —
  `tests/test_key_custody.py::test_bash_tool_cannot_print_the_key`)

- **The module-level capability gate never drained.** `_GATE` accumulated a
  `PendingConfirm` per classified command that nothing popped. Per-call now, and
  it drains in a `finally`. (#11 — `tests/test_permission.py::test_the_gate_drains`)

### Hooks and events

- **A `PreTool` hook could not block a tool.** Exit codes were collected and
  ignored, making the event advisory despite its name. It blocks now, composed
  with the permission check as *deny wins from either side*. (#13)

- **Hooks always ran under `sys.executable`.** A shell hook was handed to Python
  and failed silently. Launch is resolved — explicit `interpreter`, then the
  executable bit so a shebang is honoured, then extension, then a loud refusal.
  (#13 — `tests/test_hooks_contract.py`)

- **`PostTool` did not fire on error paths.** Every `except` returned without
  emitting, so the stream had holes exactly where a hook would want them. The
  seven tool bodies became one `_run_tool` behind a single `try/except/finally`.
  (#13)

### Configuration and reporting

- **The Grove channel was read at two different times.** `grove.py` captured
  `_CHANNEL` at import while `send()` re-read the variable, so setting the channel
  after import passed `send()`'s guard and then posted to `""`. One reader
  (`channel_env()`), resolved per send. (#20 —
  `tests/test_grove.py::test_a_sender_built_before_the_channel_is_set_still_posts_to_it`)

- **The Bash tool schema overclaimed.** It said "Shell command to execute" while
  the implementation is `shlex.split` with `shell=False`. It now names what does
  not work, and `sh -c`/`python -c` and friends are refused as a shell in disguise.
  (#15)

- **`Read` truncated silently** at 4000 characters, so a partial file was
  indistinguishable from a whole one. Truncation is marked with the true total,
  and `Read` returns numbered lines. (#15)

- **`WILLOW_ROOT` fell back to a machine-specific path** — `~/github/willow-memory/willow`,
  which does not exist on the reference node, so the fallback resolved to nothing.
  `_resolve_willow_root()` prefers the explicit variable, then derives from
  `WILLOW_HOME`. (#7)

- **`mcp_client.shutdown()` did not wait.** It scheduled the stop event without
  joining, so the interpreter could exit before stdio teardown — the thread is a
  daemon. It joins and reports whether teardown finished. (#7)

## Unimplemented (from the runtime blueprint)

Not defects. Each is a thing the blueprint describes that has not been built.

- `--local` mode does not use the policy/hook pipeline for model-side tool calls,
  because local mode currently has no tool loop.
- `/resume` picks the first best match from local sqlite search and does not
  present an interactive chooser.
- `/export` writes a plain text transcript only; markdown formatting and
  redaction presets are not yet implemented. (Credential-shaped strings *are*
  redacted — `ratatosk/redact.py`, #9 — but that is a backstop, not a preset.)

## Not verified on hardware

Every fix above was tested in CI and none has been driven on the Termux node or
against a live Grove channel. Two are most exposed there: the `child_env`
allowlist depends on `PREFIX` and `LD_LIBRARY_PATH`, without which no child
process starts on the phone, and the hook launcher's `.sh` fallback assumes
`/bin/sh`, which lives under `$PREFIX` on Termux.
