# Ideas Backlog

This file is read by `reconciler run --repo . --doc docs/ideas.md --validate`
(willow-reconciler). Every top-level `N. ` line is an item; the prose beneath an
item is its argument and is not parsed.

Legend: ✅ shipped · 🟡 partial · (untagged) proposed

**Numbers are permanent join keys.** `reconciler/ids.py` derives
`<corpus>-ideas-<num>` from the number written on the line, so a number is an
identity, not an ordinal. Never renumber; never write a markdown-auto-numbered
list — retire a number instead and leave the gap. A legend tag counts only when
it LEADS the item text. A commit that lands an item carries its `Idea-Id`
trailer (see CONTRIBUTING.md).

The argued sections (A–G) are what a mature agent harness does well, filtered to
what actually suits a 2200-line stdlib session runtime that has to keep working
on a phone. Several develop a one-liner in the backlog (section I); those are
noted by number.

---

## A. The tool result is a prompt

The largest quality lever here, and the cheapest. Tool output is not plumbing —
it is the majority of what the model reads, and every result either improves the
next decision or degrades it. Ours were written as if a human were the only
reader.

1. ✅ **shipped**: `Read` returns numbered lines — `_numbered` in `ratatosk/tools.py`, landed in ecec4a9.

   Costs nothing, and it makes `Edit`'s `old_string` reliable, lets the model
   cite `file:line`, and makes a partial read coherent rather than a floating
   fragment.

2. `Edit` should verify, then report briefly — not `"Edited {path}"`, which is a mutating operation returning zero information about its effect.

   The measured finding is *verification*: SWE-agent's edit command rejects a
   syntactically broken edit and says why, worth +3.0 points in their ablation.
   Report a short before/after of the changed region, **not a unified diff** —
   aider's benchmarks found diff formats help strong models and actively hurt
   weak ones, and weak models are this project's target.

3. ✅ **shipped**: errors are actionable, not merely true — one error shape via `problem(fault, remedy)`, prose not a taxonomy, landed in ecec4a9.

   `ERROR: [Errno 2] No such file or directory` gives the model nothing to do
   differently. The bar was already in the tree: `old_string matches 3 times —
   must be unique` states the fault *and* the remedy. That is now the shape of
   every error `dispatch` returns; see `docs/design/hooks-catch-and-redirect.md`.

Related backlog entries: 28 (tool execution receipts), 18 (rich `/status`).

## B. Read-before-Edit, and staleness

4. Refuse an `Edit` of a file the model never `Read`, or read before it changed on disk.

   Nothing currently stops the model editing a file it never read — it can
   invent an `old_string`, and if it happens to match, the edit lands. Nothing
   detects that a file changed on disk since the model read it, so a long
   session can edit stale content silently. Record `(path -> mtime, size)` when
   `Read` succeeds; have `Edit` refuse when there is no record, or the record is
   stale. The uniqueness check we already have is half of this — it catches
   ambiguity, not blindness. One guarantee, in one place that cannot be reached
   around. Develops 34 (file operation guardrails).

## C. Interruptibility as a feature

5. 🟡 **partial**: stop a turn mid-flight and redirect *without losing the session* — Ctrl-C now ends the turn, not the session (a897374); interrupting a running tool and redirecting it is still open.

   This is the clearest line between a toy REPL and something worth leaving
   running. It matters most on a phone, watching a slow local model produce
   something wrong. The live defect (`BUGS.md`, "an interrupt during a turn
   destroys the session record") is closed. Develops 27 (crash recovery mode).

## D. Sub-turns for context isolation

6. A `Task` tool: spawn a narrow task with its own history and return only its conclusion to the parent, leaving the parent's context clean.

   The highest-value thing to *add* rather than fix. This matters **more** under
   BYOK, not less. A free-tier model with an 8k–32k window cannot hold a long
   task, but it can certainly answer "grep for X, tell me which file" in one
   line. Given the provider seam (`docs/prior-art.md` → PR 3), a `Task` tool is
   a sub-loop with a fresh history and a summarising return.

## E. Externalized plan state

7. A small structured task list the model maintains and that is re-injected each turn, so intent survives compaction by construction.

   What compaction destroys is *intent*. The notice reads "compacted 40 earlier
   messages" and the goal leaves with them. A list that was never in the
   transcript cannot be compacted out of it. Develops 16 (better compaction
   strategy), 19 (session tags and metadata).

## F. Two places we could be better than the large harnesses

Both follow from BYOK, and the big products are mostly weak here because they
assume a single provider.

8. Degrade mid-loop, and say so: a 429 falls through to the next provider *during* a tool loop, not only at session start, and the model is told it happened.

   A smaller model behaves better when it knows it is the smaller model.
   Develops 21 (local-first model fallback chain), 31 (multi-model routing
   policy).

9. Context budget is per-provider: the window belongs in the provider registry beside `supports_tools`, not in one global `_MAX_CHARS = 200_000`.

   Against an 8k-token free model that constant is not a budget, it is a
   fiction.

## G. Two cheap wins alongside those

10. Prompt caching where the provider supports it — the system prompt and tool schemas are the stable prefix; another per-provider capability flag.

11. Telemetry that reports the number which actually matters on a free tier: not dollars, but requests remaining before the rate limit.

    Develops 29 (cost and token telemetry).

## H. Deliberately not doing

Repo-wide auto-context gathering and RAG over the codebase. Expensive, and it is
the corpus's job and Nestor's — this is the session process, not the memory.
Likewise elaborate diff UIs. The 2200 lines are a feature; most of what makes a
large harness large is surface we do not want. These are not items and carry no
number on purpose.

---

## I. Backlog

12. Session replay command: rebuild a readable transcript from JSONL with filters (`--session`, `--since`, `--tool-only`).
13. ✅ **shipped**: `/resume` command — pick up prior session context from the latest JSONL/deposit automatically (command router, 0339c64; released 1.2.0).
14. Safer shell execution tiers: allowlist low-risk commands, stricter confirmation for risky patterns. 🟡 partial — argument-scoped policy rules (7461e31) and the one permission chokepoint (8d793d9) are the mechanism; no shipped tier set yet.
15. Tool result caching: memoize deterministic reads/globs during a session to cut repeated tool latency.
16. Better compaction strategy: summarize dropped turns instead of just adding a generic system note.
17. Pluggable tool packs: load optional local tool modules from a `tools.d` folder.
18. 🟡 **partial**: rich `/status` dashboard — MCP tool count and Grove receipt are shown; token/char budget usage is not.
19. Session tags and metadata: let users tag sessions (`incident`, `research`, `release`) for later retrieval.
20. Auto-deposit intervals: periodic tier-0 deposit snapshots, not only on exit.
21. Local-first model fallback chain: try preferred Ollama model, then fallback list if unavailable.
22. 🟡 **partial**: built-in transcript redaction — `ratatosk/redact.py` scrubs `Read` and `Bash` output before the model sees it; JSONL and deposit writes are not yet scrubbed.
23. "Dry-run tools" mode: preview tool calls and expected effects without executing them.
24. 🟡 **partial**: health check command — `/doctor` reports python, session dir, history db, policy, hooks, key source, Ollama and MCP state (0339c64); no end-to-end Grove send/receipt or manifest-registration probe.
25. 🟡 **partial**: session search index — `history.py` keeps a sqlite index and `search_sessions` matches id, cwd and first prompt (0339c64); no keyword index over session content.
26. ✅ **shipped**: `/sessions` command — list recent sessions with model, turns, cwd, and quick-open ID (0339c64; released 1.2.0).
27. Crash recovery mode: detect interrupted runs and offer to continue from last safe point.
28. Tool execution receipts: structured per-tool metadata (duration, exit status, bytes read/written) in JSONL.
29. Cost and token telemetry: per-turn estimates and cumulative session totals for cloud runs.
30. Prompt profile presets: named profiles for coding, triage, architecture, and research.
31. Multi-model routing policy: route tasks by intent (chat vs tool-heavy) to preferred model classes.
32. Prompt sandboxing for tools: strip or isolate tool input from prompt injection patterns.
33. Secret detection before writes: block writing obvious credentials into logs/deposits.
34. 🟡 **partial**: file operation guardrails — `Write` and `Edit` default to `confirm` through the permission chokepoint (8d793d9); deletes, chmod and outside-cwd writes have no rule of their own.
35. `/undo-last-tool` support: reversible operations for write/edit actions when possible.
36. Background job manager: start, track, and summarize long-running local commands.
37. Diff-aware context loader: automatically include recent git diff hunks in assistant context.
38. MCP tool capability map: show which tools are read-only, mutating, or privileged.
39. ✅ **shipped**: Grove heartbeat pings — `BusListener.emit_heartbeat` (51ddf16), emitted on a cadence by the `ratatosk-listen` daemon (1a85826).
40. Session event webhooks: optional local HTTP callbacks for start/end/tool events.
41. JSONL schema versioning + migrator: upgrade older sessions safely across releases.
42. 🟡 **partial**: built-in export formats — `/export` writes a plain-text transcript (0339c64); markdown, HTML report and the machine-readable audit bundle are not there.
43. Policy packs: selectable strictness templates (safe, balanced, power-user) for capability gating.
44. Plugin API docs and examples: standardized interface + starter plugins for external tools.
45. 🟡 **partial**: `/doctor` deep diagnostics — the command exists with the checks in 24; env-var, dependency and file-permission verification are not among them.
46. Snapshot tests for CLI UX: lock expected console flows for commands and tool confirmations.
47. 🟡 **partial**: end-to-end fixture harness — cassettes replay real willow-mcp results deterministically (5f30e58, `tests/cassettes/`); the MCP handshake and the tool loop itself are not yet driven by one.
48. ✅ **shipped**: release automation — release-please with the hidden set and its reasoning in `release-please-config.json`, the tag-asserting publish workflow (30c0a12), the release chain pinned by `tests/test_release_wiring.py` (db177f1), and the pr-title guard (3f29d23).

## J. Fleet and platform

49. Adopt `Idea-Id` commit trailers (fleet CONVENTION, decision-2026-09-11): a commit that lands an item here carries `Idea-Id: <corpus>-ideas-<num>`, and `.github/workflows/trailers.yml` runs `reconciler verify` on every PR.
50. Rewrite `promotion.json` host; Wave 6. It still says `"host": "safe-app-store"`. The owner decided (2026-09-12 04:55Z) that safe-app-store is archived and becomes a parts bin, never an origin. Recorded here; not changed now.
51. The fleet CI floor (decision 5): a Linux matrix derived from pyproject's Python classifiers, Windows on the floor and ceiling Pythons, a lint job with ruff pinned to an exact version, CodeQL over python and actions, and an aggregate `test` gate that fails on any leg that is not `success` — skipped and cancelled included.
