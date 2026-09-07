# Ratatosk's public surface

What a ratatosk version number promises. This is the repo-side half of
`willow-mcp/docs/design/fleet-versioning.md` **Rule 2** — *"a major bump is a
promise about callers, so the surface has to mean the thing callers hold"* — and
the row below is the one to copy into that document's table when ratatosk joins
the `[tool.willow.fleet]` roster.

Ratatosk sets `bump-minor-pre-major: false` (`release-please-config.json`), so a
breaking change to anything in the left column cuts **1.0.0** rather than
shipping as a free minor. That is what makes a consumer's `<1.0.0` pin an honest
compatibility range rather than a decoration.

## The row

| package | the surface | not the surface |
|---|---|---|
| **willow-ratatosk** | the permission seam — `ratatosk.permission` (`Verdict`, `Decision`, `NeedsConfirmation`, `check`, `enforce`) and `ratatosk.capabilities` (`ActionResult`, `PendingConfirm`, `CapabilityGate`); the `ratatosk` console entry point and its flags; the on-disk `policy.json` and `hooks.json` schemas; the session JSONL record shape | internals prefixed `_`; module layout under `ratatosk.*` beyond those two; the REPL's slash commands and their printed output; the Grove envelope wire format (owned by the protocol, not by this package) |

## Why the permission seam and not something else

`promotion.json` nominates `ratatosk.capabilities:CapabilityGate` as this
package's semantic seam. `ratatosk.permission` is the front door to it —
`CapabilityGate` classifies protocol *intents*, `PolicyStore` classifies tool
*names*, and `permission.check` is the one place those two vocabularies are
combined. A caller embedding ratatosk's gate holds `check`, so `check` is what a
major bump has to be a promise about.

The two vocabularies stay separate deliberately. They classify different things,
and merging them would make one of the two lie about what it checked.

## What callers may rely on

- **`check(tool_name, inputs, *, trusted, policy, gate) -> Decision`** never
  raises. Every failure path resolves to `Verdict.DENY` with `source="error"`,
  including an unreadable policy file. A gate that cannot decide refuses.
- **`Verdict` has exactly three members** — `ALLOW`, `CONFIRM`, `DENY`. Adding a
  fourth is breaking, because the combination rule (`_combine`: most restrictive
  wins) is total over the current three and callers may switch exhaustively.
- **`enforce` raises `NeedsConfirmation` on `CONFIRM` and returns on `ALLOW`.**
  The asymmetry with `DENY` — which is a returned value, not an exception — is
  part of the contract: a denial is an answer the model should reason about, a
  confirmation is an unfinished decision that needs a human. A caller that
  forgets to handle `NeedsConfirmation` fails closed, which is the point.
- **`Decision` is frozen.** Its `verdict`, `reason` and `source` fields are
  stable; new fields may be added, existing ones may not change meaning.
- **`--trust` is a layer below explicit rules.** It relaxes the unmatched
  default only. An explicit `confirm` rule binds regardless.

## What this is not

An OS-level chokepoint. `check` is an in-process gate: it governs what
`ratatosk.tools.dispatch` will run, and a caller that bypasses `dispatch`
bypasses it. Sandboxing (Landlock, seccomp, bubblewrap) is strictly stronger and
lives in kartikeya, not here. Say so plainly rather than letting the word "gate"
imply more than it does.
