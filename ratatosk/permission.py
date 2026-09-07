"""One place a tool call is permitted or refused.

This is ratatosk's semantic seam (`promotion.json` nominates
`ratatosk.capabilities:CapabilityGate`, which this module is the front door
for), so the names here are a public surface: `Verdict`, `Decision`,
`NeedsConfirmation`, `check`. Anything underscore-prefixed is not.

The defect this exists to close: `dispatch` branched on `deny` and `allow` and
nothing else, so `PolicyStore`'s third verdict — `confirm`, which is also its
default for any unmatched tool — fell through the bottom of the `if` chain and
executed. The confirmation prompt lived in `prompt_and_dispatch` and keyed off
a `trusted` flag rather than the verdict, which meant two things:

- under `--trust`, an explicit `Write -> confirm` rule was decoration; and
- any caller of the exported `dispatch` — a listener handler, a test, a future
  daemon — got Write and Edit with no confirmation at all.

Two rules follow from that, and both are deliberate.

**The strategy is named, not implied by branch order.** `_combine` states it
once: deny from either source wins, then confirm from either, and only
allow-from-both allows. An `if/elif` chain encodes the same thing invisibly and
silently changes meaning when someone reorders it.

**CONFIRM raises; DENY returns.** Asymmetric on purpose. A denial is an answer
the model should see and reason about, so it stays a value. A confirmation is
an *unfinished* decision — it needs a human — and a caller that does not handle
it must fail closed rather than proceed. Raising is what makes forgetting to
handle it safe. `prompt_and_dispatch` is the one caller that resolves it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ratatosk.capabilities import ActionResult, CapabilityGate
from ratatosk.policy import PolicyStore
from ratatosk.protocol.envelope import Intent, build_envelope

#: Tools whose effects a later turn cannot undo on its own.
HIGH_RISK_TOOLS: frozenset[str] = frozenset({"Bash", "Write", "Edit"})


class Verdict(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str = ""
    #: Which source produced it — "policy", "gate", "trust", or "error".
    source: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


class NeedsConfirmation(Exception):
    """A human has to answer before this call can proceed."""

    def __init__(self, tool_name: str, inputs: dict, decision: Decision):
        super().__init__(f"{tool_name} requires confirmation: {decision.reason}")
        self.tool_name = tool_name
        self.inputs = inputs
        self.decision = decision


_ORDER = {Verdict.DENY: 0, Verdict.CONFIRM: 1, Verdict.ALLOW: 2}


def _combine(*decisions: Decision) -> Decision:
    """Most restrictive wins. Stated once, here, rather than as branch order."""
    return min(decisions, key=lambda d: _ORDER[d.verdict])


def _policy_decision(tool_name: str, policy: PolicyStore | None) -> Decision:
    if policy is None:
        return Decision(Verdict.ALLOW, "no policy store", "policy")
    try:
        action = policy.decide(tool_name)
    except Exception as exc:
        # A broken policy file must not open the gate.
        return Decision(Verdict.DENY, f"policy unreadable: {exc}", "error")
    if action == "deny":
        return Decision(Verdict.DENY, f"policy denied tool: {tool_name}", "policy")
    if action == "allow":
        return Decision(Verdict.ALLOW, f"policy allows {tool_name}", "policy")
    return Decision(Verdict.CONFIRM, f"policy requires confirmation for {tool_name}", "policy")


def _gate_decision(tool_name: str, inputs: dict, gate: CapabilityGate | None) -> Decision:
    """The capability gate speaks about protocol intents, not tool names.

    Kept as a separate vocabulary rather than merged into PolicyStore: they
    classify different things, and collapsing them would make one of the two
    lie about what it checked.
    """
    if gate is None or tool_name != "Bash":
        return Decision(Verdict.ALLOW, "", "gate")
    env = build_envelope(
        to="local",
        prompt=inputs.get("command", ""),
        intent=Intent.SHELL.value,
        requires_confirm=True,
    )
    try:
        action = gate.classify(env)
    except Exception as exc:
        return Decision(Verdict.DENY, f"capability gate failed: {exc}", "error")
    finally:
        # Drain. The gate used to be a module-level singleton whose `pending`
        # dict grew an entry for every classified command and was never popped,
        # so a long session accumulated every rejected command string.
        gate.reject(env.trace_id)
    if action == ActionResult.REJECTED:
        return Decision(Verdict.DENY, "shell blocked by capability gate", "gate")
    if action == ActionResult.QUEUED_CONFIRM:
        return Decision(Verdict.CONFIRM, "shell requires confirmation", "gate")
    return Decision(Verdict.ALLOW, "", "gate")


def check(
    tool_name: str,
    inputs: dict,
    *,
    trusted: bool = False,
    policy: PolicyStore | None = None,
    gate: CapabilityGate | None = None,
) -> Decision:
    """Decide whether `tool_name` may run. The only place that decides.

    `trusted` (`--trust`) is a layer *below* explicit rules: it relaxes the
    unmatched default, not a rule someone wrote down. Previously it overrode
    everything, which made an explicit `Write -> confirm` rule a no-op — the
    opposite of what writing the rule was for.
    """
    try:
        policy_decision = _policy_decision(tool_name, policy)
        gate_decision = _gate_decision(tool_name, inputs, gate)
    except Exception as exc:  # pragma: no cover - defensive
        return Decision(Verdict.DENY, f"permission check failed: {exc}", "error")

    combined = _combine(policy_decision, gate_decision)

    if trusted and combined.verdict is Verdict.CONFIRM:
        explicit = policy is not None and _has_explicit_rule(tool_name, policy)
        if not explicit:
            return Decision(Verdict.ALLOW, "--trust relaxes the unmatched default", "trust")
        return Decision(combined.verdict, f"{combined.reason} (explicit rule; --trust does not override)", combined.source)

    return combined


def _has_explicit_rule(tool_name: str, policy: PolicyStore) -> bool:
    from fnmatch import fnmatch

    try:
        return any(fnmatch(tool_name, rule.pattern) for rule in policy.load())
    except Exception:
        return True  # unreadable policy: treat as explicit, i.e. do not relax


def enforce(
    tool_name: str,
    inputs: dict,
    *,
    trusted: bool = False,
    policy: PolicyStore | None = None,
    gate: CapabilityGate | None = None,
) -> Decision:
    """`check`, but CONFIRM raises. Callers that forget to handle it fail closed."""
    decision = check(tool_name, inputs, trusted=trusted, policy=policy, gate=gate)
    if decision.verdict is Verdict.CONFIRM:
        raise NeedsConfirmation(tool_name, inputs, decision)
    return decision
