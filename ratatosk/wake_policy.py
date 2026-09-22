"""Per-seat WAKE POLICY — what a woken crown may run without a human to confirm.

Sealed 2026-09-22, pair `3566adb5`: a wake carries the seat's WAKE POLICY, a
per-seat allow/deny set read from the seat's manifest at wake time, never
from the WAKE envelope and never from the bus. Under a wake every tool
verdict that would prompt is judged against that set: allowed runs;
anything else is refused and inked (never asked, never silently dropped).
The set is the seat's registry role in tool terms — Bash is never in a
wake policy anywhere; Kart is the shell.

The seat's manifest lives in the trust root
(``$WILLOW_HOME/mcp_apps/<seat>/manifest.json``, owned by another uid,
PGP-signed) — crown must not read it, and this module does not. What crown
has at wake time is ``session_enter``'s own response: a ``role`` string,
and — when the broker starts surfacing the manifest's own policy through
that response (a willow-mcp change, explicitly out of scope for this
packet) — an explicit ``wake_policy`` object that wins over the table
below. Until then, ``load()``/``resolve_for_role()`` is the whole answer:
a role-keyed default table, validated the way ``ladder.load_ladder`` reads
``provider_ladder.json`` — refuse, don't guess.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

WAKE_POLICY_PATH = Path(__file__).parent / "wake_policy.json"

#: Never grantable through any wake policy — Kart is the shell, a wake is
#: not. A table (role-default or manifest-carried) naming Bash anywhere is
#: refused at load/validation time, not honored at dispatch time.
FORBIDDEN_TOOLS = frozenset({"Bash"})

#: The one recognized write scope today: the target path must resolve
#: under the worktree the packet's own assignment names. ``None`` means
#: Write/Edit are refused outright — there is no scope to confine them to.
WRITE_SCOPE_WORKTREE = "packet_worktree"
_VALID_WRITE_SCOPES = frozenset({WRITE_SCOPE_WORKTREE, None})

#: Tool names this module ever consults write_scope for. Any other tool's
#: allow-membership is the whole answer — no path scoping applies to it.
SCOPED_TOOLS = frozenset({"Write", "Edit"})


class WakePolicyError(ValueError):
    """The wake policy table, or one role's entry in it, is not usable."""


@dataclass(frozen=True)
class WakePolicy:
    role: str
    allow: frozenset[str]
    write_scope: str | None
    #: "role_default" (wake_policy.json) or "manifest" (session_enter's own
    #: explicit wake_policy key) — inked in the seat receipt so a reader
    #: knows which source decided.
    source: str = "role_default"

    def permits(self, tool_name: str) -> bool:
        return tool_name in self.allow

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "allow": sorted(self.allow),
            "write_scope": self.write_scope,
            "source": self.source,
        }


def _validate_entry(role: str, raw: object) -> WakePolicy:
    if not isinstance(raw, dict):
        raise WakePolicyError(f"wake policy for role {role!r} is not an object")
    allow = raw.get("allow")
    if not isinstance(allow, list) or not all(isinstance(a, str) and a for a in allow):
        raise WakePolicyError(
            f"wake policy for role {role!r}: 'allow' must be a list of tool names"
        )
    allow_set = frozenset(allow)
    forbidden = allow_set & FORBIDDEN_TOOLS
    if forbidden:
        raise WakePolicyError(
            f"wake policy for role {role!r} names forbidden tool(s) "
            f"{sorted(forbidden)} — Bash is never grantable through a wake policy"
        )
    write_scope = raw.get("write_scope")
    if write_scope not in _VALID_WRITE_SCOPES:
        raise WakePolicyError(
            f"wake policy for role {role!r}: write_scope must be "
            f"{WRITE_SCOPE_WORKTREE!r} or null, got {write_scope!r}"
        )
    return WakePolicy(role=role, allow=allow_set, write_scope=write_scope)


def load(path: str | Path | None = None) -> dict[str, WakePolicy]:
    """Read and validate the role-default wake policy table.

    Raises ``WakePolicyError`` on anything malformed: a missing file, bad
    JSON, an empty/missing ``roles`` object, a non-object role entry, a
    forbidden tool, or a bad ``write_scope`` — never a bare exception from
    ``json``/``pathlib``, and never a silently-empty table.
    """
    p = Path(path) if path else WAKE_POLICY_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise WakePolicyError(f"cannot read wake policy {p}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WakePolicyError(f"wake policy {p} is not valid JSON: {exc}") from exc
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict) or not roles:
        raise WakePolicyError(f"wake policy {p}: 'roles' must be a non-empty object")
    return {role: _validate_entry(role, raw) for role, raw in roles.items()}


def resolve_for_role(table: Mapping[str, WakePolicy], role: str) -> WakePolicy:
    """The role's policy, or ``WakePolicyError`` — a role missing from the
    table refuses the wake rather than letting it run unrestricted."""
    policy = table.get(role)
    if policy is None:
        known = ", ".join(sorted(table)) or "(none)"
        raise WakePolicyError(
            f"no wake policy for role {role!r} — known roles: {known}"
        )
    return policy


def from_manifest(raw: dict, *, role: str) -> WakePolicy:
    """A ``WakePolicy`` built from an explicit ``wake_policy`` object in
    ``session_enter``'s own response (the seat's manifest, surfaced by the
    broker — not built here; see this packet's follow-on note). Same
    validation as a table entry; ``source`` stamped ``"manifest"``."""
    validated = _validate_entry(role, raw)
    return WakePolicy(
        role=validated.role,
        allow=validated.allow,
        write_scope=validated.write_scope,
        source="manifest",
    )
