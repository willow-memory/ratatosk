"""states.py -- the three states, once (flatten F5, F-18).

Every reader in the fleet answers one of three, never collapsed
(willows-grove INVARIANTS §1): it found rows (``populated``), it looked and
there were none (``empty``), or it could not look (``unreachable``). Empty is
not unreachable: "no rows" and "no store" are different answers, and a
reader that says the first when the truth is the second has hidden a fault.

The one script, the-table and willows-grove's Grove import these; the
operator: "2 a".
"""

from __future__ import annotations

POPULATED, EMPTY, UNREACHABLE = "populated", "empty", "unreachable"
STATES = (POPULATED, EMPTY, UNREACHABLE)

# Richer row words, kept as written (flatten-2 F9, D-e: "persisted bytes
# never change"). Each is a NAMED VARIANT of one of the three: the word stays
# in the row, and code that compares across readers asks ``base_of(word)``.
RANKED = "ranked"  # a ranker found hits: populated
HELD = "held"  # a write-back with a verifying seal: populated
NONE = "none"  # no write-back answers the bite: empty
# Not a state and not (yet) a variant: the operator is deciding whether
# "escalated" is a fourth outcome or a step after a state, so it is NAMED here
# and kept out of STATES and VARIANTS until that is ruled. Pile and seed write
# it as the answer of a bite with no key; lookup carries it through.
ESCALATED_COLD_START = "escalated:cold_start"
VARIANTS = {RANKED: POPULATED, HELD: POPULATED, NONE: EMPTY}


def base_of(word: object) -> str:
    """The state a row word stands for: one of the three reads as itself, a
    variant (``ranked``, ``held``, ``none``) as the state it names, and any
    other word as ``unreachable`` (as ``checked``)."""
    return checked(VARIANTS.get(word, word))  # type: ignore[call-overload]


def state_of(rows: object) -> str:
    """``populated`` or ``empty`` for rows a reader got back; None (the reader
    could not look) is ``unreachable``. A reader that failed passes None,
    never ``[]``."""
    if rows is None:
        return UNREACHABLE
    return POPULATED if len(rows) else EMPTY  # type: ignore[arg-type]


def checked(state: object) -> str:
    """``state`` if it is one of the three; anything else reads as
    ``unreachable``: a state nobody can name is not a found or an empty."""
    return state if state in STATES else UNREACHABLE  # type: ignore[return-value]
