# A hook catches something

**Status: proposed.** This is a position, not a decision. It was drafted by an
agent from a working session and the maintainer has not signed it. Nothing here
is settled until it is.

---

## The word

A hook is a thing that catches something. It holds what it caught, and it
changes where that thing goes. It is not a wall — a wall stops.

The hook runtime in this repo is built as a wall, and a broken one: exit codes
are collected into `HookResult` and the caller discards them, so a `PreTool`
hook cannot even stop a tool. Worse than the defect is the mismatch. Every hook
mechanism in this fleet has been built as blocking, and blocking has been
underperforming, and the reason is in the name we chose and stopped reading.

"Hook" was a security term before it was a software-engineering one. API
hooking, `LD_PRELOAD`, detours, IAT patching — hooking has always meant
*interposing behaviour at a call site so something does what it wasn't going to
do*. The lifecycle-hook sense borrowed a word that already meant subversion and
made it sound benign by putting it in a config file.

That borrowing is exact, because the two are the same operation:

> **A hook and a prompt injection are both behaviour inserted at a decision
> point by a party who is not the one making the decision.**

Registration point, interposition, altered control flow. The mechanism is
identical. What differs is only channel and authorization: a hook arrives
through a path the operator controls, ahead of time, and runs with the
operator's authority; an injection arrives through the data path, at runtime,
and runs with the *agent's*.

And note what we already do with that difference. **Nobody validates what a hook
script says.** Not pre-commit, not git, not us. A hook is trusted entirely
because of where it came from, and not at all because of what it contains.
Provenance over content — which is the correct model, and precisely the model
this codebase does *not* apply to tool results, bus envelopes, or MCP output,
all of which are trusted by arrival.

`willow-mcp`'s `external_guard.py` is what running out of provenance looks like:
a regex denylist for `ignore\s+(your|the|all)\s+(instructions?...)`, trying to
validate content because there was no provenance left to lean on.

## The proposal, and what happened to it

The proposal was: stop blocking, start injecting. A hook should catch the
moment and put text into the context that changes what the model wants to do
next — sanctioned prompt injection, same channel as the attack, opposite
provenance.

Four parallel surveys were run against it: one on what shipping harnesses
actually do, one on the literature, one on the empirical evidence for feedback
quality, and one adversarial. The proposal did not survive intact. It survived
**split in two**, which is a better answer than it started as.

### What held

**The mechanism is real and convergent.** Every mature harness that iterated
past a first version arrived here independently. Claude Code's hook return
surface carries `permissionDecisionReason`, `additionalContext`, `updatedInput`
and a `retry` flag that explicitly tells the model it may try a denied call
again. Guardrails AI ships a six-way `on_fail` taxonomy — `reask`, `fix`,
`filter`, `refrain`, `noop`, `exception`. PydanticAI raises `ModelRetry` and
feeds the failure back. **DSPy deprecated `Assert`** — the hard-failure
primitive — in favour of `Refine`, which auto-generates corrective feedback when
nothing meets threshold. That is a completed block→steer migration in a major
framework, and no migration in the opposite direction was found.

**Anthropic's own tool-use documentation states the premise outright:**

> *"Write instructive error messages. Instead of generic errors like `failed`,
> include what went wrong and what Claude should try next... This gives Claude
> the context it needs to recover or adapt without guessing."*

And, separately: *"If a tool request is invalid or missing parameters, Claude
will retry 2-3 times with corrections before apologizing to the user."* That is
the failure mode we have been living with, documented by the vendor. A bare
denial does not stop a model. It produces two or three flailing retries and a
give-up.

The shape is instructive too: `is_error: true` and `content` coexist in one
payload. The boolean does not stop anything — it labels the content as less
trustworthy. All the steering is in the string. So the primitive is not
`allow | deny`; it is `(allow | deny, message)`.

**And the framing appears to be ours.** Everywhere the survey looked, steering
hooks and injection attacks are filed as unrelated concerns — one developer
experience, one security. In Anthropic's own docs the "write instructive error
messages" guidance and the indirect-prompt-injection warning about `tool_result`
content sit on the same page, treated as separate topics. Same channel, same
mechanism, one page apart, connection undrawn.

### What broke

**The privilege a trusted injection can claim is a lean, not a wall.**
Instruction hierarchy is trained into current models — tool output *is* ranked
below system and developer text — but independent evaluation recovers attack
success in the 20–75% range against compound and adaptive attacks. Anthropic's
own browser-use numbers are the most honest available: ~1.4% successful
injection for Opus 4.5 under current safeguards, against 10.8% for Sonnet 4.5
under the previous generation, alongside their own statement that *"no browser
agent is immune to prompt injection."*

Two things follow. The residual is not zero, and the vendor says so. And the
direction of travel is steep, which means **the steering tier improves as models
improve while the blocking tier stays constant** — the boundary drawn below can
move, but only one way, and only on evidence.

**Recency is frequently the attacker's advantage, not ours.** A `PreTool` hook
naturally injects *before* the tool call — which is earlier than the attacker's
text already sitting in the tool result that made the hook fire. A `PostTool`
warning about content the model just read competes against text the model
attended to most recently, from the source it trusts most: its own last
observation. Position discipline is worth having and is a per-model maintenance
obligation, not a structural guarantee.

**And the strongest objection, which deserves stating in full:**

> A hook that blocks is a control the runtime enforces regardless of what the
> model thinks; its correctness does not depend on model behaviour at all. A
> hook that injects steering text is a control whose effectiveness depends
> entirely on the same black-box arbitration the attacker is trying to
> manipulate, through the same input surface. You have not added a second,
> independent line of defence. You have added a second competitor to a contest
> the attacker was already trying to win.

Every serious defence proposal surveyed — the six design patterns, CaMeL,
Willison's dual-LLM, the lethal trifecta — recommends moving the highest-stakes
decisions *out* of the context window's arbitration entirely. That is the
opposite of sanctioned injection.

## The position

**Two primitives, split by one criterion.**

> **An action must be hard-blocked if the cost of a single silent success is not
> recoverable by a later turn in the same session** — if the world outside
> Ratatosk's own state has changed in a way Ratatosk cannot revoke.

Not "high risk." Irreversibility.

The criterion was derived twice, independently, from opposite directions. The
adversarial survey reached it from attack surface: what can a single silent
success cost. The literature survey reached it from feedback efficacy: explained
refusals measurably help recovery from *task* failures, while for *security*
refusals there is evidence that explaining why a gate fired teaches the boundary
to route around — with the recommended countermeasure being generic,
non-diagnostic errors precisely to prevent that learning. Same line, two
derivations.

### Tier 1 — hard block, outside the context window

Enforced by the harness in Python, before the action runs, whether or not the
model reads or agrees with anything. Refusal is **generic**: state that it was
refused, not the rule that refused it.

Members, by the criterion:

- Credential and key material — reading it at all, once any egress path exists.
  Two legs of the lethal trifecta are already live here: untrusted content
  arrives via tool results and the Grove bus, and the bus itself is an
  exfiltration channel.
- Any write to the hook configuration, or to a path a hook entry resolves to as
  executable. This one is self-referential and must never be gateable by the
  mechanism it would compromise.
- Outbound Grove sends. `from_agent` is a self-asserted string on an unsigned
  envelope, so an outbound message is indistinguishable from a forged one at the
  receiver — the sending side is the only enforcement point that exists.
- Irreversible destructive operations on state outside our own sandbox.

**This is where `fail closed` bites, and where steering structurally cannot
satisfy it.** An injection with no verdict to inject is simply *absent*, and an
absent nudge is indistinguishable from silent approval. Only a hard gate has a
defined refuse state when its own check fails.

### Tier 2 — catch and redirect

Everything else: the large surface of judgment calls that were never good
candidates for a binary gate, where being wrong costs a turn rather than a
secret. This is where blocking has been failing us and where injection wins.

Requirements, from the literature:

- **Spotlight it.** Hook-injected text wrapped in a consistent, distinct
  delimiter scheme, separate from the scheme marking untrusted content.
  Microsoft's spotlighting work took attack success from >50% to <2–3% —
  against non-adaptive attackers, on 2024-era models, outside an agentic loop.
  Cheap, real, and not a guarantee.
- **Place it last**, closest to generation, deliberately claiming recency rather
  than inheriting whatever position falls out of the implementation.
- **Frame it as system/developer tier**, so it inherits what instruction
  hierarchy training the backing model has.
- **Demote untrusted content explicitly**, and strip it once its data is
  extracted (Context-Minimisation) rather than leaving raw untrusted text in the
  window beside the redirect.
- **Never let raw untrusted content occupy instruction position.** This is the
  one invariant every source agrees on. If a tool result ever appears in a role
  or format indistinguishable from an instruction, every defence above is void.

### Three rules that constrain both tiers

**Never build hook text from untrusted content.** `"the file you read contains:
{content}"` hands the attacker authorship of the trusted channel. There is no
semantic equivalent of escaping — you cannot quote natural language such that a
model will not be steered by it. Reference tool output structurally (a hash, a
fixed-vocabulary classification produced by the hook's own code), never by
copy-through.

**The trusted marker must be unforgeable from tool-output space.** Not
conventionally distinct — provably disjoint. Otherwise an attacker emits the
preamble and speaks as the hook.

**Reject hostile Unicode at parse time, not display time.** Bidi overrides
(U+202A–202E, U+2066–2069), zero-width (U+200B–200D, U+FEFF), and the Tags block
(U+E0000–E007F), in hook scripts, hook config, Grove envelope content, and MCP
tool descriptions at registration. Fail closed on the whole file — silent
stripping is itself a parse-time behaviour an attacker can plan around. The
Rules File Backdoor research (Pillar, disclosed to Cursor Feb 2025 and GitHub
Mar 2025) showed invisible Unicode in rules files steering both Cursor and
Copilot into emitting backdoored code, never mentioned in the assistant's reply.
GitHub's fix was a display-layer warning; it did not stop models parsing the
hidden text. Rejecting at parse time is strictly better and costs one stdlib
function.

## Hook config is a persistence mechanism, and it is not fully solvable

Stated plainly because softening it would be the overclaim.

If the agent can write files, it can write hooks — and an injection then
persists across sessions with operator authority, and under this proposal with
elevated standing to steer the model. Our closest sibling tool took two CVEs for
exactly this shape within the last year: **CVE-2025-59536** (CVSS 8.7), where a
malicious repository's `.claude/settings.json` `SessionStart` hook with a
`startup` matcher executed shell commands the moment the CLI ran in a freshly
cloned directory; and **CVE-2026-21852** (CVSS 5.3), information disclosure in
the project-load flow letting a malicious repo exfiltrate data including the API
key — which compounds precisely with the environment-inheritance bug already in
`BUGS.md`.

The pattern is older than AI tooling. Lazarus's "Contagious Interview" campaign
plants payloads in `.git/hooks/pre-commit` and `post-checkout` that fire on
clone or checkout, in the wild, and reported as expanding into AI tooling
configs. VS Code's `tasks.json` with `runOn: folderOpen`. direnv's `.envrc`.

**Every mitigation deployed against any of them is either a trust prompt that
degrades through habituation, or containment of what the triggered code can do.
None prevents the write.** The honest claim is "raises the cost and requires an
out-of-session step," not "closes the hole."

Best available containment, all stdlib:

- The **harness** — not a hook, which would be circular — refuses
  agent-originated writes to the hook config and to anything a hook entry
  resolves to.
- Hooks are content-hash-pinned at approval; drift disables them until
  re-approved. This defeats the rug-pull even though it does not stop a
  first-time malicious write.
- A new hook entry requires confirmation through a channel the model cannot
  satisfy. This is our own rule, already written for decisions and not yet
  applied here: **you may propose, you may not confirm.**
- Hook subprocesses get a minimal explicit `env=`, which also closes the key
  disclosure already logged.

And note the proposal makes one thing worse and we should say so: today a hook's
stdout is discarded, so a naive or compromised hook script is a modest target.
Feeding its output to the model as authoritative raises the payoff without
changing the cost of compromise.

## The tool-result standard

The same insight, one layer down: **a tool result is not plumbing.** It is most
of what the model reads, and ours are written as though a human were the only
reader.

Only one item here is measured. Tagged accordingly, because our rule is never to
claim what we cannot support.

| Change | Evidence | Note |
|---|---|---|
| `Edit` verifies and reports briefly instead of `"Edited {path}"` | **MEASURED** | SWE-agent's edit-with-linting ablation: **+3.0 points**. A mutating op currently returns zero information about its effect. |
| One error shape: prose stating fault **and remedy** | **INFERRED** | Three independent teams converge; no ablation of this exact change. `"old_string matches 3 times — must be unique"` is the target, already in the tree. |
| Explicit truncation marker on `Read` | **INFERRED** | Unmeasured, but practitioner consensus is unanimous. SWE-agent special-cases empty output for the same reason: silence is ambiguous. |
| Line numbers on `Read` | **INFERRED** | Mechanism plus universal practice; no isolated number. |
| Return a diff from `Edit` | **FOLKLORE** | Aider's benchmarks: diff formats help strong models and **hurt weak ones**. Weak models are our target. Short before/after, not a patch. |
| Machine-readable error `kind` field | **FOLKLORE, evidence against** | Rigid structured formats measurably cost accuracy through task interference. The reader is a language model. Do not add; do not claim. |

Two further findings from SWE-agent's own docs, both arrived at independently
and both cheap: empty command output is never returned as nothing — it is
replaced with an explicit statement that the command succeeded silently — and
their directory search was made deliberately *terse* because excessive context
confused the model. Richer is not the goal. Legible is.

### The experiment worth more than more reading

Nobody has ablated tool-result informativeness against model capability. The
open question is whether richer results matter *more* for weaker models, which
would be decisive for a runtime that has to work on a phone.

Twenty to thirty multi-step tasks this runtime already handles, run twice per
model tier — one strong, one small and local — with current bare-string results
against verified-edit-with-context, marked truncation, and unified prose errors.
Measure completion rate and turns-to-recovery. Same runtime, no new
infrastructure.

An in-house n=30 is worth more to our documentation than any further literature
search, because it attaches a number to *these* tool results rather than
borrowing SWE-agent's by analogy.

## What this changes in the plan

`docs/prior-art.md` → **PR 5 (Hooks)** grows a tier model and stops being purely
an ergonomics fix. `PreTool` gains a context-injection return alongside its
verdict — the one thing Claude Code's own hook surface still lacks, and which
its users have filed for. The hard-block tier and the write-protection of hook
config become part of that PR rather than a later one.

`docs/BUGS.md` gains no new defects from this note; the error-shape entry is
corrected to prose rather than a machine-readable kind.

## Sources

Verified where the survey could reach a primary source. Two of the four surveys
had their fetch access blocked and worked from search summaries quoting primary
sources — the quantitative figures from the instruction-hierarchy, spotlighting,
CaMeL, SWE-agent and Reflexion literature are **secondary-sourced and should be
re-checked against the papers before any of them are quoted outward.**

Harnesses: Claude Code hooks docs, Guardrails AI, NeMo Guardrails, PydanticAI,
DSPy, OpenAI Agents SDK, Semantic Kernel, LangGraph, OpenHands microagents,
Cursor and Cline rules, aider conventions.

Literature: Wallace et al., *The Instruction Hierarchy* (2024); Hines et al.,
*Spotlighting* (2024); Beurer-Kellner et al., *Design Patterns for Securing LLM
Agents against Prompt Injection* (2025); Debenedetti et al., *CaMeL* (2025);
Willison, *the lethal trifecta* (2025); Yang et al., *SWE-agent* (2024); Shinn
et al., *Reflexion* (2023); Gou et al., *CRITIC* (2023); Huang et al., *LLMs
Cannot Self-Correct Reasoning Yet* (2024).

Incidents: CVE-2025-59536, CVE-2026-21852, Pillar Security's *Rules File
Backdoor*, Invariant Labs on MCP tool poisoning and the GitHub MCP exploit,
Lazarus git-hooks persistence.
