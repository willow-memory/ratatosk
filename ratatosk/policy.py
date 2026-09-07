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
