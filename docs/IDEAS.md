# Ideas Backlog

The flat list below is the raw queue. This section is the part with an argument
attached — what a mature agent harness does well, filtered to what actually
suits a 2200-line stdlib session runtime that has to keep working on a phone.
Several of these develop a one-liner already in the backlog; those are noted.

---

## The tool result is a prompt

The largest quality lever here, and the cheapest. Tool output is not plumbing —
it is the majority of what the model reads, and every result either improves the
next decision or degrades it. Today ours are written as if a human were the only
reader.

- **`Read` should return numbered lines.** Costs nothing, and it makes `Edit`'s
  `old_string` reliable, lets the model cite `file:line`, and makes a partial
  read coherent rather than a floating fragment.
- **`Edit` should return the changed region**, a few lines of context either
  side — not `"Edited {path}"`. The model has just modified a file and has no
  idea what it now says, so it either re-reads (a wasted turn and more context
  spent) or proceeds blind.
- **Errors should be actionable, not merely true.** `ERROR: [Errno 2] No such
  file or directory` gives the model nothing to do differently. The good version
  is already in the tree: `old_string matches 3 times — must be unique` states
  the fault *and* the remedy. That is the bar for the rest.

Related backlog entries: *tool execution receipts*, *rich `/status`*.

## Read-before-Edit, and staleness

Nothing currently stops the model editing a file it never read — it can invent
an `old_string`, and if it happens to match, the edit lands. Nothing detects
that a file changed on disk since the model read it, so a long session can edit
stale content silently.

Record `(path -> mtime, size)` when `Read` succeeds; have `Edit` refuse when
there is no record, or the record is stale. The uniqueness check we already have
is half of this — it catches ambiguity, not blindness. One guarantee, in one
place that cannot be reached around.

Develops: *file operation guardrails*.

## Interruptibility as a feature

Stopping a turn mid-flight and redirecting *without losing the session* is the
clearest line between a toy REPL and something worth leaving running. It matters
most on a phone, watching a slow local model produce something wrong.

This is also a live defect — see `BUGS.md`, "an interrupt during a turn
destroys the session record."

Develops: *crash recovery mode*.

## Sub-turns for context isolation

The highest-value thing to *add* rather than fix. Spawn a narrow task with its
own history and return only its conclusion to the parent, leaving the parent's
context clean.

This matters **more** under BYOK, not less. A free-tier model with an 8k–32k
window cannot hold a long task, but it can certainly answer "grep for X, tell me
which file" in one line. Given the provider seam (`docs/prior-art.md` → PR 3), a
`Task` tool is a sub-loop with a fresh history and a summarising return.

## Externalized plan state

What compaction destroys is *intent*. The notice reads "compacted 40 earlier
messages" and the goal leaves with them. A small structured task list that the
model maintains and that is re-injected each turn survives compaction by
construction, because it was never in the transcript to begin with.

Develops: *better compaction strategy*, *session tags and metadata*.

## Two places we could be better than the large harnesses

Both follow from BYOK, and the big products are mostly weak here because they
assume a single provider.

- **Degrade mid-loop, and say so.** A 429 should fall through to the next
  provider *during* a tool loop, not only at session start — and the model
  should be told it happened. A smaller model behaves better when it knows it is
  the smaller model.
- **Context budget is per-provider.** `_MAX_CHARS = 200_000` is one global
  constant. Against an 8k-token free model that is not a budget, it is a
  fiction. The window belongs in the provider registry beside `supports_tools`.

Develops: *local-first model fallback chain*, *multi-model routing policy*.

## Two cheap wins alongside those

- **Prompt caching** where the provider supports it — the system prompt and tool
  schemas are the stable prefix, and it is a large cost cut on the providers that
  offer it. Another per-provider capability flag.
- **Telemetry that reports the number which actually matters on a free tier:**
  not dollars, but requests remaining before the rate limit.

Develops: *cost and token telemetry*.

## Deliberately not doing

Repo-wide auto-context gathering and RAG over the codebase. Expensive, and it is
the corpus's job and Nestor's — this is the session process, not the memory.
Likewise elaborate diff UIs. The 2200 lines are a feature; most of what makes a
large harness large is surface we do not want.

---

## Backlog

- Session replay command: rebuild a readable transcript from JSONL with filters (`--session`, `--since`, `--tool-only`).
- `/resume` command: pick up prior session context from latest JSONL/deposit automatically.
- Safer shell execution tiers: allowlist low-risk commands, stricter confirmation for risky patterns.
- Tool result caching: memoize deterministic reads/globs during a session to cut repeated tool latency.
- Better compaction strategy: summarize dropped turns instead of just adding a generic system note.
- Pluggable tool packs: load optional local tool modules from a `tools.d` folder.
- Rich `/status` dashboard: include MCP tool count, Grove channel health, token/char budget usage.
- Session tags and metadata: let users tag sessions (`incident`, `research`, `release`) for later retrieval.
- Auto-deposit intervals: periodic tier-0 deposit snapshots, not only on exit.
- Local-first model fallback chain: try preferred Ollama model, then fallback list if unavailable.
- Built-in transcript redaction: scrub secrets/paths before writing JSONL/deposit exports.
- “Dry-run tools” mode: preview tool calls and expected effects without executing them.
- Health check command: end-to-end diagnostic for MCP connectivity, manifest registration, and Grove send/receipt.
- Session search index: lightweight local index so you can grep prior sessions by keyword instantly.
- `/sessions` command: list recent sessions with model, turns, cwd, and quick-open ID.
- Crash recovery mode: detect interrupted runs and offer to continue from last safe point.
- Tool execution receipts: structured per-tool metadata (duration, exit status, bytes read/written) in JSONL.
- Cost and token telemetry: per-turn estimates and cumulative session totals for cloud runs.
- Prompt profile presets: named profiles for coding, triage, architecture, and research.
- Multi-model routing policy: route tasks by intent (chat vs tool-heavy) to preferred model classes.
- Prompt sandboxing for tools: strip or isolate tool input from prompt injection patterns.
- Secret detection before writes: block writing obvious credentials into logs/deposits.
- File operation guardrails: require explicit confirmation for deletes, chmod, and outside-cwd writes.
- `/undo-last-tool` support: reversible operations for write/edit actions when possible.
- Background job manager: start, track, and summarize long-running local commands.
- Diff-aware context loader: automatically include recent git diff hunks in assistant context.
- MCP tool capability map: show which tools are read-only, mutating, or privileged.
- Grove heartbeat pings: periodic liveness signal with last-turn timestamp.
- Session event webhooks: optional local HTTP callbacks for start/end/tool events.
- JSONL schema versioning + migrator: upgrade older sessions safely across releases.
- Built-in export formats: markdown transcript, HTML report, and machine-readable audit bundle.
- Policy packs: selectable strictness templates (safe, balanced, power-user) for capability gating.
- Plugin API docs and examples: standardized interface + starter plugins for external tools.
- `/doctor` deep diagnostics: verify env vars, dependencies, model availability, and file permissions.
- Snapshot tests for CLI UX: lock expected console flows for commands and tool confirmations.
- End-to-end fixture harness: deterministic tests for MCP handshake and tool loop behavior.
- Release automation: changelog generation, version bump checks, and publish pipeline.

