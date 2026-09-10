"""Persistent tool policy rules.

A pattern is a tool-name glob (`Bash`, `Danger*`) and may carry an argument
scope in parentheses: `Bash(git status*)`, `Write(/etc/*)`. Both halves are
`fnmatch` globs — one matcher, so there is only one thing to learn.

The scope is additive on purpose. `policy.json` is part of this package's
declared public surface, so a pattern written before scopes existed has to
keep meaning exactly what it meant: an unscoped `Bash` still matches every
Bash call, and `decide` still returns the first rule that matches.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from ratatosk.paths import ratatosk_data_root

#: `Name(arg glob)`. A tool name never contains parentheses, so a pattern that
#: does not match this is an ordinary name glob and is treated as one.
_SCOPED = re.compile(r"^(?P<name>[^()]+)\((?P<arg>.*)\)$", re.DOTALL)

#: The one input field a scoped rule reads, per tool. A tool absent from this
#: table has no defined subject, so a scoped rule cannot match it at all —
#: rather than guessing a field and granting on a coincidence.
_SUBJECT_FIELDS = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
}

#: Shell syntax that turns one command into several, or redirects it. A glob
#: over the raw string cannot see past these.
_SHELL_CONTROL = (";", "&&", "||", "|", "`", "$(", ">", "<", "&", "\n")


@dataclass
class PolicyRule:
    pattern: str
    action: str  # allow|deny|confirm


@dataclass(frozen=True)
class ParsedPattern:
    name: str
    #: The argument glob, or None for an unscoped rule (matches any arguments).
    arg: str | None

    @property
    def scoped(self) -> bool:
        return self.arg is not None


def parse_pattern(pattern: str) -> ParsedPattern:
    """Split `Bash(git status*)` into its two globs. Unscoped patterns pass through."""
    found = _SCOPED.match(pattern.strip())
    if found is None:
        return ParsedPattern(name=pattern.strip(), arg=None)
    return ParsedPattern(name=found.group("name").strip(), arg=found.group("arg").strip())


def subject_field(tool_name: str) -> str | None:
    """The input field a scoped rule reads for this tool, or None if undefined."""
    return _SUBJECT_FIELDS.get(tool_name)


def rule_subject(tool_name: str, inputs: dict | None) -> str | None:
    """The single input a scoped rule matches against, or None if undefined."""
    field = _SUBJECT_FIELDS.get(tool_name)
    if field is None or not inputs:
        return None
    value = inputs.get(field)
    return str(value) if isinstance(value, (str, int, float)) else None


def is_simple_command(command: str) -> bool:
    """True when a glob over the whole string can be trusted to describe it.

    `Bash(git*)` looks like it permits git. Against `git status; rm -rf ~` a
    naive prefix glob says yes, because the string does start with `git`.
    """
    return not any(token in command for token in _SHELL_CONTROL)


def rule_matches(rule: PolicyRule, tool_name: str, inputs: dict | None = None) -> bool:
    """Whether `rule` answers this call.

    An unscoped rule matches on the tool name, exactly as it always has.

    A scoped rule additionally matches its argument glob against the tool's
    subject, and refuses in two situations rather than guessing:

    - **No subject.** The tool has no declared subject field, or the call did
      not supply one. The rule does not match, so the search moves on and the
      unmatched default (`confirm`) still backstops it.
    - **A compound command being allowed.** A scoped `allow` never applies to a
      command carrying shell control syntax, because the glob cannot see past
      it. `deny` and `confirm` still apply — narrowing what a compound command
      may do is safe, widening it is not. The asymmetry is the point: you
      cannot buy a blanket allow by chaining onto a permitted prefix.
    """
    parsed = parse_pattern(rule.pattern)
    if not fnmatch(tool_name, parsed.name):
        return False
    if not parsed.scoped:
        return True

    subject = rule_subject(tool_name, inputs)
    if subject is None:
        return False
    if tool_name == "Bash" and rule.action == "allow" and not is_simple_command(subject):
        return False
    return fnmatch(subject, parsed.arg or "")


@dataclass(frozen=True)
class ShadowedRule:
    """A rule `decide` can never reach, and the earlier rule that eats it."""

    index: int
    rule: PolicyRule
    by_index: int
    by: PolicyRule


def _eats(earlier: ParsedPattern, later: ParsedPattern) -> bool:
    """Whether `earlier` answers everything `later` would have."""
    if not fnmatch(later.name, earlier.name):
        return False
    if not earlier.scoped:
        return True  # answers every call under that name, scoped or not
    if not later.scoped:
        return False  # the narrower rule leaves the rest to the broader one
    return fnmatch(later.arg or "", earlier.arg or "")


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

    Scopes follow the same conservatism. An unscoped rule eats any scoped rule
    under a matching name, because it answers every call the narrower one
    wanted. The reverse never holds: a scoped rule leaves whatever it does not
    match to the rules below it.
    """
    found: list[ShadowedRule] = []
    for later_index, later in enumerate(rules):
        for earlier_index, earlier in enumerate(rules[:later_index]):
            if _eats(parse_pattern(earlier.pattern), parse_pattern(later.pattern)):
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

    def decide(self, tool_name: str, inputs: dict | None = None) -> str:
        """The action for this call. `inputs` is optional and additive.

        Called without inputs — as every caller written before scopes existed
        does — scoped rules simply cannot match, and the answer is the same one
        that caller has always received.
        """
        for rule in self.load():
            if rule_matches(rule, tool_name, inputs):
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
