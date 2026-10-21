"""``states_local`` held to the one script's ``states.py`` (flatten-2, reverse pass on F5).

The reverse pass found ratatosk restating the three states (``onescript.py``'s
``STATES`` and its ``_doc("empty", ...)`` calls, ``redact.py``'s
``unreachable``). They mean the reader's state, so they come from one place.
But ratatosk must keep working as an installed package with no one script
beside it (operator: "carry copies"), and the-table's rule is that an absent
one script is said ``unreachable``, never a hard dependency. So the code uses
``ratatosk/states_local.py``, a byte copy of ``onescript/states.py`` (the
pattern of willow-mcp's ``scrub_local``), and this file holds the copy to it,
source and values. The copy is edited in the one script and copied out.

Locally, with no willow-bot beside, the parity tests skip and say so; in CI
(``CI`` is set) they fail instead, as ``test_onescript_reach`` does.
"""

import ast
import os
from pathlib import Path

import pytest

from ratatosk import onescript_reach, redact, states_local

PKG = Path(states_local.__file__).resolve().parent


def _one():
    try:
        return onescript_reach.module("states")
    except onescript_reach.Unreachable as e:
        if os.environ.get("CI"):
            pytest.fail(f"CI has no willow-bot beside: {e}")
        pytest.skip(f"no willow-bot beside (set ONESCRIPT_HOME): {e}")


def test_the_copy_is_the_one_scripts_bytes():
    one = _one()
    assert Path(states_local.__file__).read_bytes() == Path(one.__file__).read_bytes()


def test_the_copy_has_the_same_values():
    one = _one()
    for name in ("POPULATED", "EMPTY", "UNREACHABLE", "STATES", "VARIANTS"):
        assert getattr(states_local, name) == getattr(one, name), name
    for rows in (None, [], [1]):
        assert states_local.state_of(rows) == one.state_of(rows)
    for word in ("populated", "ranked", "none", "zz", None):
        assert states_local.checked(word) == one.checked(word)
        assert states_local.base_of(word) == one.base_of(word)


def test_the_words_are_what_ratatosk_always_wrote():
    # behaviour pin: the literals the package used to carry
    assert states_local.STATES == ("populated", "empty", "unreachable")
    assert redact.found("") == []
    from ratatosk import onescript

    assert onescript.STATES == ("populated", "empty", "unreachable")
    assert onescript._doc(states_local.UNREACHABLE, "x")["state"] == "unreachable"


def _scan_restated_states(path: Path) -> list[int]:
    """Lines of a string constant equal to a state word, outside a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and n.value in states_local.STATES
        and not isinstance(parent.get(n), ast.Expr)
    ]


@pytest.mark.parametrize("name", ["onescript.py", "redact.py"])
def test_no_state_word_is_restated_in_code(name):
    """Carved out: ``onescript_reach.py`` (a byte-copied template that cannot
    import) and ``states_local.py`` (the copy itself)."""
    bad = _scan_restated_states(PKG / name)
    assert not bad, f"{name} restates a state word at line(s) {bad}"


def test_the_scan_sees_a_planted_restatement(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        '"""empty in a docstring is prose."""\n'
        'x = {"state": "unreachable"}\n'
        'y = "seeded"\n'
        'z = r == "populated"\n'
    )
    assert _scan_restated_states(f) == [2, 4]
