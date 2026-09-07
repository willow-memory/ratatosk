# Known Bugs

## Correctness and safety

- **The listener answers its own messages.** `BusListener.run_once`
  (`ratatosk/listener.py:147`) filters incoming messages on `id > cursor` and
  nothing else — there is no `sender != self.node` check. `parse_grove_message`
  (`ratatosk/protocol/envelope.py:229`) turns any message that is neither JSON
  nor `name: prompt` into a chat envelope addressed to the default node, so a
  reply posted at `listener.py:115` is re-fetched on the next poll, parsed as a
  fresh prompt for this node, and answered. Replay detection does not catch it:
  the self-parse mints a new nonce via `build_envelope`. On a live Grove channel
  under `--mcp --listen` this is an unbounded reply loop. `termux/boot/
  ratatosk-listen.sh` starts exactly that mode.

- **`confirm` is enforced at the call site, not in dispatch.** `PolicyStore`
  ships `Bash`/`Write`/`Edit` as `confirm` and defaults unknown tools to
  `confirm`, but `dispatch` (`ratatosk/tools.py:123`) branches only on `deny`
  and `allow` — a `confirm` verdict falls through and executes. The prompt lives
  in `prompt_and_dispatch`, and it keys off `trusted` rather than the policy
  verdict, so under `--trust` a `Write -> confirm` rule is a silent no-op, and
  any caller of the exported `dispatch` gets no confirmation on Write or Edit at
  all. Bash is incidentally covered by `_bash_allowed`; Write and Edit are not.
  The rule belongs in `dispatch`, where it cannot be reached around. No test
  covers the `confirm` verdict — `tests/test_tools.py` exercises `deny` and
  `allow` only.

- **Replay protection fails open on a missing nonce.** `validate_envelope`
  (`ratatosk/protocol/envelope.py:268`) guards the replay check with
  `if check_replay and env.nonce:`, and `_envelope_from_dict` defaults an absent
  nonce to `""`. Omitting the field skips the check entirely. Related:
  `_remember_nonce` evicts with `set.pop()`, which removes an arbitrary element
  rather than the oldest, so at the 10k cap a nonce seen seconds ago can be
  dropped while stale ones survive. Wants FIFO eviction.

- **`_compact` can orphan a `tool_result`.** `crown.py:32` keeps
  `history[-(_MAX_TURNS * 2):]`. It only runs when the last message carries no
  `tool_use` block, so the trailing edge is safe, but the leading edge can land
  on a user message of `tool_result` blocks whose matching `tool_use` was just
  dropped. The API rejects that, so a long tool-heavy session fails at the point
  compaction first triggers.

- **An interrupt during a turn destroys the session record.** `_run_turn` is
  wrapped in `except Exception` (`ratatosk/crown.py:415`), and
  `KeyboardInterrupt` is a `BaseException`. A Ctrl-C mid-turn therefore
  propagates out of the REPL loop entirely, skipping `mcp_client.shutdown()`,
  `session_ended`, the `SessionEnd` hook, the `--deposit` write, and
  `index_session`. The JSONL survives because it is written per entry, but it is
  never indexed — so `/sessions` and `/resume` cannot see it, and the tier-0
  deposit never happens. The moment you most want to interrupt is the one that
  costs you the session. The cleanup block wants to be a `try`/`finally`, and
  the interrupt wants catching so the turn can be abandoned without the session
  being abandoned with it.

- **`dispatch()` returns three different error shapes.**
  `json.dumps({"error": ...})` for policy denial and user denial, a bare
  `f"ERROR: {exc}"` for every tool failure, and `f"[stub] tool '{name}' not
  wired"` for an unknown tool (`ratatosk/tools.py`). The model cannot reliably
  distinguish "I called this wrongly" from "the tool broke" from "that tool does
  not exist" — three situations with three different next moves.

  One shape, and — on the evidence — **prose stating the fault and the remedy**,
  not a machine-readable error taxonomy. The model in front of these strings is
  a language model, and rigid structured formats measurably cost accuracy by
  making it parse a schema while it reasons. `"old_string matches 3 times — must
  be unique"`, already in this file, is the target shape. See
  `docs/design/hooks-catch-and-redirect.md` → *The tool-result standard*.

- **The API key is handed to every subprocess.** `_load_api_key`
  (`ratatosk/crown.py:66`) reads a key out of a credentials file and writes it
  into `os.environ`. Neither the Bash tool (`ratatosk/tools.py:147`) nor the
  hook runtime (`ratatosk/hooks.py:49`) passes an explicit `env=`, so both
  inherit it — a model-invoked `Bash` call running `env` prints the key into
  the transcript, and under `--trust` it does so without asking. Fix is two
  parts: do not put a file-loaded secret into the process environment, and pass
  an allowlisted `env=` to both `subprocess.run` sites. This gets worse with
  per-provider keys; see `docs/prior-art.md` → *Key custody*.

- **The module-level capability gate never drains.** `_GATE`
  (`ratatosk/tools.py:86`) is process-wide, and every untrusted Bash call runs
  `classify()`, which inserts a `PendingConfirm` that nothing ever pops. A long
  session accumulates envelopes holding every rejected command string.

## Hooks and events

- **A `PreTool` hook cannot block a tool.** `HookRuntime.run_event`
  (`ratatosk/hooks.py:38`) collects exit codes into `HookResult` and `dispatch`
  ignores them, so the pre-tool event is advisory only — not what the name
  implies to anyone configuring one.

- **Hooks always run under `sys.executable`.** A shell hook is launched as a
  Python script and silently fails.

- **`PostTool` does not fire on error paths.** Every `except` branch in
  `ratatosk/tools.py` returns without emitting the event, so the stream has
  holes exactly where a hook would want them.

## Configuration and reporting

- **Grove channel is read at two different times.** `grove.py` captures
  `_CHANNEL` at import while `send()` re-reads `RATATOSK_GROVE_CHANNEL` at call
  time. Setting the channel after import passes the `send()` guard while
  `make_mcp_sender` posts to `""`.

- **The Bash tool schema overclaims.** Its description says "Shell command to
  execute" while the implementation is `shlex.split` with `shell=False`. No
  pipes, redirects, or globs — the schema should say so.

- **`Read` truncates silently.** `ratatosk/tools.py:169` cuts at 4000 characters
  with no marker, so the caller cannot tell a partial file from a whole one.

- **`WILLOW_ROOT` falls back to a machine-specific path.** `crown.py:20`
  defaults to `~/github/willow-memory/willow`.

- **`mcp_client.shutdown()` does not wait.** It schedules the stop event without
  taking the future's result, so the process can exit before stdio teardown.

## Unimplemented (from the runtime blueprint)

- `--local` mode does not use the policy/hook pipeline for model-side tool calls
  because local mode currently has no tool loop.
- `/resume` picks the first best match from local sqlite search and does not
  present an interactive chooser.
- `/export` writes a plain text transcript only; markdown formatting and
  redaction presets are not yet implemented.
