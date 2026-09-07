# Prior art, and the plan it implies

A survey against the defects in [`BUGS.md`](BUGS.md), done to answer one
question per defect: **has somebody already solved this better, and can we
take it?**

The short answer across six defect clusters: almost nothing *outside* this
fleet is worth vendoring, and almost everything is worth reading. Every
recommendation drawn from a third party below is a design ported into our own
stdlib code, not a file copied in. That is not license caution — it is what the
survey found. The good solutions are either entangled with a host framework's
data model (OpenHands' event graph, PydanticAI's toolsets), written in another
language (Codex CLI, goose), or so small once understood that copying costs
more than writing.

The exceptions are all our own prior work. Three of the six answers are
already in the live fleet, and a seventh problem — running on any provider's
key rather than Anthropic's — turns out to be largely solved in `willow-2.0`,
which is Apache-2.0 and therefore a straight copy. Those come first.

---

## Licensing position

This repo is Apache-2.0 as of the relicense commit; `willow-mcp` and `Nestor`
are too. Code can move between the three without a seam.

The archives differ, and it matters:
[`rudi193-cmd/willow-2.0`](https://github.com/rudi193-cmd/willow-2.0) is
**Apache-2.0** with a `LICENSE` and `NOTICE`;
[`rudi193-cmd/willow-1.9`](https://github.com/rudi193-cmd/willow-1.9) is
**PolyForm Noncommercial 1.0.0**. Both are public and archived, both are the
same copyright holder. Taking from 2.0 is a plain copy needing only a `NOTICE`
entry. Taking from 1.9 would need an explicit relicense by the copyright
holder — the precedent exists in `willow-mcp`'s own `NOTICE`, which relicenses
the Nest and `mai` code out of PolyForm on exactly that basis, but it is a
decision to record, not a formality. **Nothing below needs it**: see the
finding that the two archives carry the same code.

For outside code: MIT/BSD/ISC drops in with its copyright line retained.
Apache-2.0 drops in retaining its own header, with a `NOTICE` entry.
GPL/AGPL/LGPL/SSPL/BUSL do not drop in at all — `python-telegram-bot`,
`Errbot`, `borgmatic`, `git` and `systemd` appear below for their designs
only, and no code from them may be used.

None of this is currently load-bearing, because nothing below is a vendor.

---

## Already in the fleet

### `grove_listen.py` — the self-post filter (BUGS §1)

`willow_mcp/grove_listen.py` already carries the guard this repo is missing:

```python
own_post = sender.lower() == me
```

Its `classify()` docstring states the rule outright — *"A seat's own posts never
wake it"* — and every branch below that line is gated on `not own_post`. It
also uses the identical cursor design (`WHERE id > cursor`, so coalesced or
missed notifies are harmless).

Our listener is that module with the guard lost in transit. Take the guard, and
take the shape too: `classify()` is deliberately pure, no I/O, *"so it is the
whole of what the tests pin."* Our `process_message` should split the same way
— classification separable from polling, so the adversarial cases can be
tested exhaustively without a bus.

### `dispatch_signing.py` — the signed/unsigned migration (BUGS: envelope auth)

159 lines, stdlib only. `_canonical_bytes()` is `json.dumps(signable,
sort_keys=True, separators=(",", ":"))` with HMAC-SHA256 over every field but
the signature.

More useful than the crypto is the **tri-state status model**: `valid` /
`invalid` / `legacy_unsigned`. An unsigned packet is kept out of the trusted
list but not hard-refused, unless `WILLOW_MCP_STRICT_TRUST_ROOT=1`. A packet
with a present-but-*wrong* signature is always refused, strict mode or not —
because that is tamper evidence, not an upgrade artifact.

That is the answer to "how do v1-unsigned and v2-signed coexist," already
reasoned through. Take it.

### `nestor/signing.py` + `keyring.py` — the staged crypto (BUGS: envelope auth)

HMAC by default with stdlib only; Ed25519 behind a `[keys]` extra, imported
lazily. `_message()`'s byte encoding is explicitly **frozen and documented so
an out-of-process client can reproduce it** — added so a browser doing
WebCrypto Ed25519 could seal without the private key ever reaching the
instance. That is exactly the shape a Ratatosk node needs.

`keyring.py` also carries the revocation model, which is the part nobody
invents until they need it: `compromised=False` means rotate, and everything
already signed still stands; `compromised=True` means nothing it signed is
distinguishable from the thief's, so none of it serves — and the rows are not
deleted, they surface for re-verification.

### One thing NOT to take from the fleet

`willow-mcp` has `cryptography>=42.0,<51.0` as a **hard runtime dependency**,
imported at module level in `egress_setup.py` and `egress_authorization.py`.
`cryptography` builds from source via Rust on Termux, where Android target
triples are not auto-detected and the build routinely fails; prebuilt
`manylinux` aarch64 wheels do not apply, because Termux is not a manylinux
platform.

Ratatosk is the Termux node. So the fleet's *dependency posture* does not port
even though its *code* does. Nestor's posture is the one to copy: optional
extra, lazy import, HMAC default, never a silent degrade to unsigned.

**PyNaCl (Apache-2.0) is the better Ed25519 dependency for this repo** — no
Rust, cffi over libsodium. But its Termux story is also unproven: manylinux
aarch64 wheels exist and still may not install under Termux. Do not claim
`[sign]` works on a phone until someone has run it on one.

### Canonicalization drift, worth fixing in willow-mcp

`dispatch_signing.py` canonicalizes with `sort_keys=True, separators=(",",":")`.
`bound_receipt.py` uses `separators=(",",":"), ensure_ascii=False`. Neither
NFC-normalizes strings, and neither rejects duplicate JSON keys. Three drift
points in one repo, in the one place where drift is silent — a signature from
one will never verify in the other. Not this repo's bug, but this repo should
not inherit it: pick one rule (below) and put it somewhere all three can call.

---

## Bring your own key — mostly already written

The goal: no hard dependency on Anthropic, or on any one vendor. A user brings
whatever key they have — including a free one — and Ratatosk runs. Today
`crown.py` has exactly two paths, `anthropic` or Ollama, and the tool loop
speaks Anthropic's content-block format natively.

`willow-2.0` already built most of this, in stdlib, and its
`core/inference_router.py` docstring states the thesis outright:

> *"Priority (`WILLOW_INFERENCE_PROVIDER`): local → Ollama only · cloud →
> Gemini → Groq (70b) → OpenRouter-compatible fleet keys · auto → Ollama, then
> cloud chain. **No Anthropic required. Any OpenAI-compatible or Gemini REST
> key works.**"*

### What to take, and from where

| Module | Lines | What it is |
|---|---|---|
| `core/model_adapter.py` | 219 | `ModelAdapter` ABC + Ollama / Anthropic / Groq / xAI / **OpenAI-compatible** implementations + `get_adapter()` factory. Pure `urllib.request`. |
| `core/inference_router.py` | 234 | The fallback chain. `local` / `cloud` / `auto` modes; returns `(text, provider_used)` so the caller knows who answered. |
| `core/providers.py` | 177 | Provider registry persisted in the store: enable/disable, key masking, per-provider model lists. |
| `tests/test_model_adapter.py` | 67 | Bring them. |
| `tests/test_providers.py` | 120 | Bring them. |

**Take all of it from 2.0, not 1.9.** The two archives carry the *same* seam:
`model_adapter.py` differs by two hunks (a default model id, one extra entry in
`available_models()`), `providers.py` by a single list item. Since the code is
identical and 2.0 is Apache-2.0 while 1.9 is PolyForm, copying from 2.0 removes
the relicensing question entirely.

1.9 has **no fallback chain at all** — no `inference_router.py`, no
`llm_edge.py`, and nothing equivalent hiding in `fleet.py` or
`sap/middleware.py`. The router is genuinely new in 2.0.

### Three details worth keeping

- **`chat()` returns `(text, provider_used)`.** A fallback chain that does not
  report which link answered is unauditable, and this one does.
- **Enabled ≠ installed.** `providers.py` treats an enabled-but-unreachable
  Ollama as a skip, not an error — *"Ollama being enabled means the user wants
  it, not that it's installed"* — and Ollama cannot be disabled at all, because
  it is the local-first floor.
- **Free-tier key rotation.** `scripts/groq_agent.py` resolves
  `GROQ_API_KEY or GROQ_API_KEY_2 or GROQ_API_KEY_3`. Unglamorous, and it is
  the detail that makes a free tier actually usable under rate limits.

### Four things to fix on the way in

1. **Trim the router's fleet couplings.** `_try_fleet` (imports
   `sap.clients.professor_client`) and `_try_hns` (imports `core.store_port`,
   `willow.hns_scheduler`, `willow.hns_enforcer`) are fleet-internal branches
   that mean nothing to a standalone session runtime. Dropping both leaves
   `_try_ollama`, `_try_gemini`, `_try_groq`, `_try_openrouter`, `_chain` and
   `chat` — stdlib-only and self-contained. About a 60-line trim.
2. **The credential vault degrades silently.** `sap/core/inference.py`'s
   `load_credential` tries the Fernet-encrypted SQLite vault, falls back to a
   plaintext `credentials.json`, then to the environment — each inside a bare
   `except: pass`. On Termux, where `cryptography` will not build, that is an
   unannounced downgrade from encrypted storage to a plaintext file. Make the
   fallback explicit and loud: a degrade the operator chose, not one that
   happened to them. Same rule as everywhere else here — never a silent
   fail-open.
3. **`AnthropicAdapter.health()` makes a real billed API call** to check
   liveness. On a metered free tier that is quota spent on a ping.
4. **Drop the `__import__("os")` line.** `CODEX_REPO` on line 26 of
   `sap/core/inference.py` uses inline `__import__` for `os` and `pathlib` in a
   file that already imports both at the top. Present in 1.9 and 2.0 alike, so
   it is an original wart rather than a regression — do not carry it forward.

One file 2.0 dropped, `ganas_client.py`, was correctly dropped: its `chat()`
returned bracketed error strings *as content* (`"[ganas2 unavailable — …]"`),
so a failure became model-visible text instead of a signal. The 2.0 adapters
return `None` or raise, which is what a chain needs in order to fall through.

### The part that is genuinely new work

`ModelAdapter.chat()` returns a `str`, and `inference_router.chat(system,
user)` takes two strings. **There is no tool use, no streaming, and no
multi-turn history anywhere in it.**

So `willow-2.0` solved BYOK for *single-turn chat*. Ratatosk needs BYOK for a
*tool-using agent loop*, which is strictly harder, and harder exactly where the
wire formats diverge: Anthropic represents a tool round as content-block arrays
(`tool_use` blocks in an assistant message, `tool_result` blocks in the
following user message), while OpenAI-compatible endpoints use `tool_calls` on
the assistant message plus separate `role: "tool"` messages keyed by
`tool_call_id`. Those are not two dialects of one shape. One flat list cannot
be sent to both.

Widening the seam therefore means:

- `chat()` takes a **neutral history** and a tool-schema list, and returns
  text *plus* any tool calls, rather than a bare string.
- Each adapter serializes the neutral form to its own wire format at the edge,
  and parses tool calls back out of it.
- A `supports_tools` capability flag per provider, checked up front — plenty of
  free endpoints do not do tool use, and refusing clearly beats a 400 mid-loop.
- Streaming as an optional adapter capability, with a non-streaming fallback,
  since not every endpoint offers it.

### What this does to the compaction fix

It settles it. The survey argued *against* restructuring history into
exchanges, on the grounds that a wrapper type touches every append, read and
serialize site while the boundary scan is fifteen lines. That reasoning held
for a single backend. With two wire formats a neutral internal representation
is no longer optional — and once history is a list of exchanges rather than a
flat list of messages, **the orphaned-`tool_result` bug cannot be expressed.**
You cannot slice between a tool call and its result when they are one object.

So the compaction fix stops being a fix and becomes a consequence of the
provider seam. Keep the pre-send validation pass anyway (smolagents' PR #1132
is a dangling `tool_result` produced by a construction bug, no truncation
involved), but the boundary scan is no longer the design.

This also retracts one earlier note: Anthropic's server-side context editing
only helps on the Anthropic path, and free tiers tend to have *small* context
windows, so local compaction gets **more** load-bearing under BYOK, not less.

### Key custody, which BYOK makes urgent

`crown.py`'s `_load_api_key` reads a key out of a file and writes it into
`os.environ` (line 66). Neither the Bash tool nor the hook runtime passes an
explicit `env=`, so both inherit it. A model-invoked `Bash` call running `env`
dumps the key into the transcript, and under `--trust` it does so without
asking. Today that is one key; under BYOK it is every key a user has
configured.

Fix it here rather than in PR 6: stop writing file-loaded keys into the
process environment, and pass an allowlisted `env=` to both `subprocess.run`
sites. That is the minimal-env hardening PR 6 wants anyway, so it lands once
and serves both.

And `willow-2.0` already has the regression test for this class:
`scripts/verify_public_fallback.py` scans a pack for `gsk_`, `sk-ant-`, the
operator's home path, and bare `GROQ_API_KEY=`-style assignments. Port it.

---

## The plan

### PR 1 — Bus hygiene (BUGS §1, §3)

**Two layers, because either failing alone should not be catastrophic.**

1. **Poll loop:** drop any message whose sender is this node, *before* parsing.
   This is Slack Bolt's placement — `IgnoringSelfEvents` is default middleware
   checking two independent identity fields at the framework boundary, not
   per-handler. discord.py is the counter-example: it ships no self-filter at
   all, which is why `if message.author == client.user: return` appears in every
   tutorial ever written about it.
2. **Parser:** "unaddressed message defaults to me" becomes a **hard
   rejection**. An addressing scheme exists to disambiguate a multi-party
   channel; treating "I could not tell who this is for" as "therefore it is for
   me" makes the parser's failure mode maximally permissive exactly where the
   risk is a resource-exhausting loop. Google's A2A avoids the question entirely
   by routing to a per-agent endpoint — no content-sniffed fallback exists to
   get wrong.

The nonce layer **cannot** catch this bug and should not be asked to: the
self-parse mints a fresh nonce.

**Nonce cache:** `OrderedDict` keyed by nonce, value = first-seen timestamp.
Evict with `popitem(last=False)` — true FIFO, unlike `set.pop()`'s arbitrary
element. **Time-windowed primarily, count-capped only as a memory backstop.**
NATS JetStream chose a time window (`Nats-Msg-Id`, default 2 min,
operator-tunable) deliberately: message *rate* varies, message *staleness* is
what matters. A count cap alone evicts a nonce seen one second ago under load
while keeping hours-old ones when quiet. ~20 lines of stdlib; do not add
`cachetools` for it.

**Persist it.** An in-memory-only cache silently reopens the replay window on
every restart. `sqlite3` is stdlib, and the cursor should commit alongside it so
a crash cannot reset replay protection independently of the read position.

**Missing nonce → reject.** Not "skip the check." What breaks: producers that
omit the field start failing, which is correct — make emission mandatory rather
than tolerate a silent bypass. Any migration window gets an explicit, logged,
time-boxed flag, never a default-empty-string.

**Also add a hop counter.** Even with correct self-filtering, two *different*
participants can ping-pong forever. AG2 has `max_round` and
`allow_repeat_speaker=False` as first-class constructor arguments; LangGraph
has `recursion_limit` (default 25) raising `GraphRecursionError` rather than
looping silently. A bounded depth in the envelope, rejected past N, and loud
when it trips.

### PR 2 — The permission chokepoint (BUGS §2)

**`dispatch()` becomes the chokepoint.** It calls `PolicyStore.verdict()` as
its first action. `prompt_and_dispatch` shrinks to a CLI-only resolver that
supplies the *how* of answering a `CONFIRM` and never decides the verdict.

**`CONFIRM` becomes a return value, not a blocking call.** This is the best
idea in the whole survey. PydanticAI's `ApprovalRequiredToolset` does not
prompt — the run *terminates early* and returns `DeferredToolRequests` listing
pending calls by id; the caller decides and resumes with `DeferredToolResults`.
OpenHands does the same through conversation state
(`WAITING_FOR_CONFIRMATION`) rather than blocking a thread on stdin.

So `dispatch()` returns/raises a resumable `NeedsConfirmation` and never calls
`input()`. The property this buys: an API, a daemon, or a test that does not
handle it **fails closed automatically**. There is no caller shape that
silently proceeds — enforced structurally, not by remembering.

**`--trust` becomes a rule layer, not a bypass.** Insert it *below* explicit
user rules in precedence, turning only the unmatched-*default* `CONFIRM` into
`ALLOW`. An explicit `Write → confirm` still binds under `--trust`. gptme is the
cautionary version of what we have now: one global flag in the wrapper that
turns the rule set into decoration.

**Two gates, one verdict — do not merge their vocabularies.** `CapabilityGate`
classifies protocol intents; `PolicyStore` classifies tool names. They classify
genuinely different things. Put both behind one `permission.check(context) ->
Verdict` that `dispatch()` calls once, with the combination strategy **named in
code**, not implied by `if`/`elif` order (Casbin's lesson): *deny from either
wins; confirm from either requires confirm; only allow-from-both allows.*

**Fail closed on internal errors → `DENY`,** not `CONFIRM` and never allow.
OpenHands defaults to `HIGH` risk when its analyzer raises.

**Argument-aware rules:** Continue's `Bash(git status*)` syntax layers onto the
`fnmatch` already here, and gets us "allow `git status`, not `git push`".

**State the ceiling honestly in the PR text.** Codex CLI's chokepoint is the
operating system — Landlock/seccomp, Seatbelt — so no internal call path can
bypass it regardless of control flow. This is an in-process gate and is weaker.
Say so rather than overselling it.

### PR 3 — The provider seam and BYOK

Vendor `core/model_adapter.py`, `core/providers.py` and a trimmed
`core/inference_router.py` from `willow-2.0` (Apache-2.0, `NOTICE` entry
owed), with their two test files. Drop `_try_fleet` and `_try_hns`. Make the
credential-vault fallback loud instead of silent. Fix the key-into-`environ`
leak and pass an allowlisted `env=` to both `subprocess.run` sites. Port
`verify_public_fallback.py` as the secret-leak regression gate.

Then widen `chat()` to carry a neutral history, tool schemas, tool calls out,
and optional streaming, with a `supports_tools` flag checked before a
tool-using turn is attempted. Serialize to each wire format at the adapter
edge.

Full detail in **Bring your own key** above. This is the largest single change
in the plan and it subsumes most of PR 4.

### PR 4 — Compaction (BUGS §4)

**Mostly falls out of PR 3.** Once history is a list of exchanges, the
orphaned-`tool_result` bug cannot be expressed. What remains is the
pre-send validation pass and the budget accounting below. The boundary-scan
algorithm is kept here as the fallback design if the seam lands later than
this fix.

**First, check whether we need most of it.** The Anthropic API reportedly
carries server-side context editing (`context_management`, strategy
`clear_tool_uses_20250919`) that clears old tool results past a token threshold,
always keeps the most recent N tool-use/result pairs, and applies *after* cache
lookup so prefixes survive. **UNVERIFIED — beta, and nobody here has run it.**
If it holds, the cloud path becomes a request parameter and `_compact` demotes
to an Ollama-only fallback.

**The local fix, either way:** scan backward for a safe truncation boundary,
then run a validation pass over the kept slice asserting no `tool_result`'s
`tool_use_id` is unmatched. If validation still fails, keep one more exchange
rather than send.

**Scan at truncation time; do not restructure history into exchanges.** An
`Exchange` wrapper touches every append, read, and serialize site — including
the Ollama path, which has no tool-block shape at all. The boundary scan is ~15
lines, purely additive, and lives inside `_compact`. OpenHands and smolagents
earn the grouped model because they already keep a rich event graph for replay
and multi-agent handoff; we would be inventing one to fix a slicing bug.

Keep the validation pass even so: smolagents' PR #1132 is a dangling
`tool_result` produced by a *construction* bug, with no truncation involved.
Paired-by-construction still wants a check before the wire.

**This is a known ecosystem class, not our oversight.** LangChain's
`trim_messages` has the same defect open as issue #29637. LiteLLM shipped it and
fixed it in PR #11517 by extracting trailing tool-call messages before trimming
and re-appending them — tool exchanges pinned rather than trimmed. Aider's
`history.py` avoids it only by refusing to cut immediately after an assistant
turn, which works for chat-shaped history and would not survive a real chain.

**Budget accounting:** the system prompt and serialized tool schemas are not
counted today, and with MCP connected the schemas are potentially the largest
single contributor. `len(json.dumps(...))` once per turn, subtracted from the
budget, plus a reserve for `max_tokens` output. Keep `chars/4` as the token
proxy; offer `/v1/messages/count_tokens` as an opt-in exact measure when the
`cloud` extra is present.

### PR 5 — Hooks (BUGS: hooks and events)

**Exit code 2 is the only "deliberate block."** Any other nonzero means the
hook crashed. Conflating the two means a broken hook silently denies — this
distinction is the single most important thing to take from Claude Code's hook
spec, which is the closest published analogue.

**Output contract:** `{"decision": "allow"|"deny", "reason": str,
"updated_input": {...}}`. `reason` required on deny and surfaced to the model.
`updated_input` is a **shallow merge over keys the tool schema already
declares** — never a free-form replacement — and the merged result is then
re-validated by the `PolicyStore` as if the hook had never run. That ordering is
Kubernetes': mutating admission webhooks run before validating ones, and only
mutating ones may modify, so whatever a mutator produces is what the validators
actually see.

**Failure semantics, per hook, explicit.** `on_failure: "deny"|"allow"`,
defaulting to **deny for `PreTool`** (it exists to gate; a check that cannot run
means no) and **allow for `PostTool`** and lifecycle events (they cannot gate
anything, and crashing a session because an audit script died serves nobody).
Timeout is treated identically to a crash — no partial credit for having
started.

**Launch heterogeneously.** Explicit `interpreter` field wins; else on POSIX
check `os.access(script, os.X_OK)` and exec directly so the shebang is honoured;
else dispatch by extension; else refuse loudly. Never `shell=True`. This is
pre-commit's `language: script`/`system` model and git's decades-old
convention.

**`PostTool` unconditional,** via `try`/`except`/`else` around dispatch — one
structural fix, not one per `except` branch. It must also fire when a `PreTool`
hook *itself* crashes (borgmatic's `on_error` covers hook-caused failures, not
just operation-caused ones). systemd is the cautionary case: `ExecStopPost` does
not reliably fire when `ExecStartPre` fails, and has generated issues for years.

**Composition with `PolicyStore`: deny always wins, from either side.** A hook
cannot override a policy deny by returning allow, and policy allow cannot
override a hook deny. Hooks may be the *more* restrictive voice, never the less.

**Trust the hook file.** It names executables to run. `os.stat` checks for
group/world-writability on the config and on each referenced script, and an
explicit opt-in before hooks from a new directory are executed at all.

### PR 6 — Shell honesty and hardening (BUGS: schema overclaim, silent truncation)

**Keep `shell=False`. Fix the schema.** This is the highest-value, lowest-risk
change in the whole set and costs nothing. The description must say: no pipes,
no redirects, no globs, no chaining, no variable expansion, no subshells;
tokenized with `shlex.split` and run directly.

The alternatives both fail here. OpenHands runs a real bash in a tmux pane and
pushes all safety onto its container — we have no container on a phone. Aider
runs `shell=True` and is safe only because `/run` is *user-typed*, never
model-invoked — the opposite of our trust boundary. If pipes are wanted later,
parse the pipeline and wire `Popen` stages in Python; separate PR, do not couple
it.

**Tiering, honestly scoped.** A short, fixed, non-configurable allowlist of
read-only binaries matched on **argv[0] only, never on arguments** — because
argument-content rules are the fragile class (`Bash(curl http://github.com/ *)`
is defeated by options before the URL, a protocol swap, a redirect, or
`URL=... && curl $URL`). Everything else asks. A hard denylist under that as a
floor, not as a grant: refuse `bash -c`, `sh -c`, `python -c`, `perl -e` outright
— those are `shell=True` in disguise and defeat the premise of the tool.

`shlex.split(posix=True)` **cannot see `&&` or `|`** — it folds them into
literal argv. If real tiering is wanted, `bashlex` (pure Python, no C
extension, Termux-clean) parses to an AST for the *classification decision
only*, never to build the argv we execute. Verify its LICENSE before adding it;
it is the one dependency plausibly worth breaking zero-deps for.

**Fail closed on anything unparseable.**

**Do not build a denylist and call it security.** open-interpreter's Safe Mode
runs semgrep over generated code and its own docs say it "does not provide any
guarantees of safety or security." Cite that in the PR as why we did not.

**Process-tree timeout.** `subprocess.run(timeout=)` signals only the direct
child; a backgrounded grandchild survives. Use `Popen(..., start_new_session=True)`
so the child leads its own process group, then `os.killpg(SIGTERM)`, wait, then
`SIGKILL`. Stdlib, works on Termux and desktop alike.

**Truncation must be signalled, every time,** in both `Bash` output and `Read`.
Head+tail beats first-N (errors cluster at the end), with a marker stating the
omitted count and the true total so the model knows to narrow rather than reason
confidently from a clipped view. One shared helper for both tools.

**Portable hardening, laptop and rootless phone:** `resource.setrlimit` via the
child, a minimal explicit `env` (strip `LD_PRELOAD` and anything not on a
passthrough list), `start_new_session=True`. Landlock and bubblewrap are
best-effort feature-detected extras on desktop Linux **only** — both need
unprivileged user namespaces or a kernel LSM that Android does not reliably
provide, and Codex demoted Landlock to a documented fallback behind bubblewrap
for exactly this kind of reason. Degrade loudly, never silently.

### PR 7+ — Envelope authentication (separate arc)

Not a bug fix; a missing layer. The envelope has the entire shape of an
authenticated message and none of the substance — `from_agent` is a
self-asserted string, and the nonce, TTL, capability list and confirm flag are
all defences that assume an authenticated sender.

**The standard library has no asymmetric signing.** `hmac` is stdlib and real;
`hashlib` hashes only; `ssl` exposes no app-level signing. Verified — do not
overstate this in either direction.

**Stage 1, stdlib only:** `v: 2` carrying a mandatory `sig`, HMAC-SHA256 over
the canonicalized envelope, key distributed the way the roster is. Unsigned
rejected by default; any compatibility flag is explicit, logged, and never
usable for `run_task`/`shell` regardless. HMAC's honest costs: no
non-repudiation, every key-holder can forge every other's messages, N² key
distribution, and revocation means rotating with everyone at once. For a small,
manually-enrolled fleet that is a legitimate tier 1.

**Stage 2, `[sign]` extra:** Ed25519 via PyNaCl, wrapped so nothing else
imports `nacl` directly, falling back to Stage 1 rather than to unsigned. Prove
it on Termux before claiming it.

**Canonicalization, precisely:** reject duplicate JSON keys at parse
(`object_pairs_hook` that raises — "last wins" is parser-defined and lets the
signature cover different intent than the reader sees); NFC-normalize strings;
ban floats from the envelope entirely so JCS's number-format subtleties never
arise; then `sort_keys=True, separators=(",", ":"), ensure_ascii=False`.

**Sign the whole envelope, `extra` included.** Enumerating "the important
fields" is the anti-pattern JWT fell into. Excluding `extra` is precisely the
"sign a subset and let an attacker append" bug. Cap `extra`'s size and depth
before signing. Exclude only the `sig` slot itself.

Every field matters and the attack for each is concrete: unsigned `to` lets a
relay replay A's message at B; unsigned `expires_at` makes the nonce cache
decorative; unsigned `capabilities` lets a grant be widened under a valid
signature; unsigned `intent` lets `chat` become `shell`; unsigned `nonce` lets
replay protection be stripped.

**Migration without `v` becoming a downgrade vector.** PASETO's discipline: a
version pins exactly one algorithm, never negotiated, because JWT's `alg` is
attacker-controlled input to the verifier's trust decision (`alg: none`, and
RS256→HS256 confusion where the public key is fed in as an HMAC secret).
Concretely: each node config states a **minimum accepted `v`**; anything below
is rejected before processing, logged as a downgrade attempt. And if the roster
says a sender is enrolled at `v:3`, an envelope claiming `v:1` from that sender
is rejected regardless of local minimum — it contradicts what is already known
about *that* sender.

**Keys and enrolment.** A signed roster file mapping node to public key,
distributed by hand — USB, paste, a QR code scanned in Termux — never fetched
over the bus. Enrolment is a local CLI command, never an MCP tool and never
triggerable by a message. That is the fleet's own sudo invariant and it is not
negotiable here. Revocation is an appended signed entry, and is only as fast as
roster distribution — state that plainly and lean on short `expires_at` so a
missed revocation self-heals. Rotation signs the new key with the old one, with
a bounded overlap.

On Termux the private key is a plain file in per-app storage. Not
hardware-backed; a rooted or ADB-debuggable phone can read it. Say so.

**What signing does not fix.** A compromised node's messages are authenticated
and obeyed. Signing proves *who*, not whether the instruction is sound. It does
nothing about an over-broad capability list (that is authorization — see
macaroon-style attenuation, where a holder may only narrow a grant, never widen
it, because the chain breaks). It does nothing about volume; that needs rate
limits. And high-risk intents still need the human-confirm path as an
independent control, not as a substitute for the authentication that was
missing.

**The open question this raises for the fleet:** three repos now need the same
canonicalize → sign → verify → roster-check logic, and `willow-mcp` already
disagrees with itself about canonicalization in two files. This is the shape
that `ARCHITECT.md` says gets lifted — *"a shared concern trapped inside
something heavy gets lifted into a stdlib-minimal core that everything can
depend on cleanly."* A small Apache-2.0 `fleet-sign` package doing exactly those
four things, and nothing else, is the obvious move. That decision belongs in
`docs/dogfood/decisions/`, not in this file.

---

## Sources

**Vendored, or to be:** `rudi193-cmd/willow-2.0` (Apache-2.0, verified from its
own `LICENSE` and `NOTICE`) — `core/model_adapter.py`, `core/providers.py`,
`core/inference_router.py`, `scripts/verify_public_fallback.py` and two test
files. `rudi193-cmd/willow-1.9` (PolyForm Noncommercial 1.0.0) was read for
comparison and nothing is taken from it, because 2.0 carries the same code
under a license that does not require a relicense decision.

Everything below is **read for design, none vendored.** Licenses verified from
the projects' own LICENSE files at time of survey.

Permissive: OpenHands (MIT), PydanticAI (MIT), Continue (Apache-2.0), Codex CLI
(Apache-2.0), goose (Apache-2.0), aider (Apache-2.0), smolagents (Apache-2.0),
Cline (Apache-2.0), Letta (Apache-2.0), open-interpreter (Apache-2.0), LangChain
(MIT), LiteLLM (MIT core — `enterprise/` is separately licensed, avoid),
cachetools (MIT), Slack Bolt (MIT), discord.py (MIT), matrix-nio (ISC), LangGraph
(MIT), AG2 (Apache-2.0), nats.py (Apache-2.0), pre-commit (MIT), pluggy (MIT),
PyNaCl (Apache-2.0), OCI runtime-spec (Apache-2.0), OPA (Apache-2.0).

Copyleft — design only, no code: python-telegram-bot (LGPL/GPL), Errbot (GPL-3),
borgmatic (GPL-3), git (GPL-2), systemd (LGPL-2.1+).

Specs and docs: Anthropic tool-use and context-editing docs, Claude Code hooks
and permissions docs, Nostr NIP-01, PASETO, RFC 8785 (JCS), Matrix canonical
JSON, Kubernetes admission webhooks, Google A2A, AT Protocol, DIDComm, MCP
authorization, Landlock, SPIFFE/SPIRE, Sigstore.

Unverified and flagged as such above: Anthropic's `context_management` beta,
`bashlex`'s license, `py-landlock`'s license, bubblewrap's license, and
PyNaCl's actual installability under Termux.
