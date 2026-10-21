"""Mask credential-shaped strings on their way out.

A backstop, not a control. The control is that a secret never enters the child
environment in the first place (`ratatosk.child_env`); this catches the cases
that control cannot see — a key pasted into a file the model reads, a token
echoed by a command that had it for its own reasons, a transcript exported to
somewhere less careful than the session directory.

The patterns are the one script's (flatten-2 F12, `onescript/scrub.py`, set
`credentials`; the operator: "Using the onescript at every path."): the nine
that were copied here from willow-mcp `secret_scan.py`, plus the journal set's
vendor keys and the Discord token, and none of them `\b`-anchored, so a key
glued to letters is masked too. Ordering matters and is the one script's: most
specific first, so a PEM block is claimed whole before a token rule can nibble
at its base64 body. The marker is still `[REDACTED:<kind>]`.

With no one script reachable, `redact` withholds the whole text and says why
(`[REDACTED:unreachable: ...]`) and `found` reports `["unreachable"]`: a
backstop that cannot look must not pass the text on, and it never falls back
to a copy of its own.
"""

from __future__ import annotations

from ratatosk import onescript_reach

PLACEHOLDER = "[REDACTED:{kind}]"
_SETS = ("credentials",)


def _scrub():
    """The one script's ``scrub`` module, or ``None`` and why."""
    try:
        return onescript_reach.module("scrub"), ""
    except onescript_reach.Unreachable as e:
        return None, str(e)


def redact(text: str) -> str:
    """Replace credential-shaped substrings with a labelled placeholder."""
    if not isinstance(text, str) or not text:
        return text
    scrub, why = _scrub()
    if scrub is None:
        return PLACEHOLDER.format(kind=f"unreachable: {why}")
    return scrub.apply(text, _SETS, marker=PLACEHOLDER)


def found(text: str) -> list[str]:
    """Which kinds `redact` would mask. For diagnostics; never logs the value."""
    if not isinstance(text, str) or not text:
        return []
    scrub, _why = _scrub()
    if scrub is None:
        return ["unreachable"]
    return scrub.found_kinds(text, _SETS)
