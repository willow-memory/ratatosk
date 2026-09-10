"""Persistent tool policy rules."""
from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from ratatosk.paths import ratatosk_data_root


@dataclass
class PolicyRule:
    pattern: str
    action: str  # allow|deny|confirm


@dataclass(frozen=True)
class ShadowedRule:
    """A rule `decide` can never reach, and the earlier rule that eats it."""

    index: int
    rule: PolicyRule
    by_index: int
    by: PolicyRule


def shadowed_rules(rules: list[PolicyRule]) -> list[ShadowedRule]:
    """Rules an earlier pattern already answers for, so `decide` never reaches them.

    `decide` returns the first pattern that matches and `set_rule` inserts at
    the front, so a broad pattern quietly makes every later rule dead. They
    still print in `/permissions list` exactly like live ones — the operator
    reads a rule that has no effect and believes it. That is the lie this
    closes; it does not change which verdict `decide` returns.

    The test is deliberately conservative: a later rule is reported only when
    an earlier pattern matches its literal text, which settles the ordinary
    cases (`*` before `Bash`, `Ba*` before `Bash*`). Patterns that merely
    overlap in part are left alone. A missed shadow prints as it does today,
    whereas a false one would accuse a live rule of being dead — and an
    operator who deletes a rule on that advice has been actively misled.
    """
    found: list[ShadowedRule] = []
    for later_index, later in enumerate(rules):
        for earlier_index, earlier in enumerate(rules[:later_index]):
            if fnmatch(later.pattern, earlier.pattern):
                found.append(
                    ShadowedRule(index=later_index, rule=later, by_index=earlier_index, by=earlier)
                )
                break
    return found


class PolicyStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (ratatosk_data_root() / "policy.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(
                [
                    PolicyRule(pattern="Bash", action="confirm"),
                    PolicyRule(pattern="Write", action="confirm"),
                    PolicyRule(pattern="Edit", action="confirm"),
                ]
            )

    def load(self) -> list[PolicyRule]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        rules = payload.get("rules", [])
        out: list[PolicyRule] = []
        for item in rules:
            pattern = str(item.get("pattern", "")).strip()
            action = str(item.get("action", "")).strip().lower()
            if pattern and action in {"allow", "deny", "confirm"}:
                out.append(PolicyRule(pattern=pattern, action=action))
        return out

    def save(self, rules: list[PolicyRule]) -> None:
        payload = {"rules": [{"pattern": r.pattern, "action": r.action} for r in rules]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def shadowed(self) -> list[ShadowedRule]:
        """The rules on disk that are unreachable. Read-only; changes nothing."""
        return shadowed_rules(self.load())

    def decide(self, tool_name: str) -> str:
        for rule in self.load():
            if fnmatch(tool_name, rule.pattern):
                return rule.action
        return "confirm"

    def set_rule(self, pattern: str, action: str) -> None:
        action = action.lower()
        if action not in {"allow", "deny", "confirm"}:
            raise ValueError("action must be one of: allow, deny, confirm")
        rules = [r for r in self.load() if r.pattern != pattern]
        rules.insert(0, PolicyRule(pattern=pattern, action=action))
        self.save(rules)

    def remove_rule(self, pattern: str) -> bool:
        rules = self.load()
        filtered = [r for r in rules if r.pattern != pattern]
        if len(filtered) == len(rules):
            return False
        self.save(filtered)
        return True
