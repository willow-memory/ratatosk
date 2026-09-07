"""Compaction never orphans a tool_result."""
from ratatosk.crown import _MAX_TURNS, _compact, _orphans, _safe_start


def _pair(n: int) -> list[dict]:
    """One assistant tool_use turn and the user tool_result that answers it."""
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": f"tu{n}", "name": "Bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"tu{n}", "content": "ok"}]},
    ]


def _tool_heavy(pairs: int, lead: int = 0) -> list[dict]:
    """A history whose turns are three messages long, not four.

    Uniform two-message pairs never reproduce the defect: the slice length is
    even, so the boundary always lands on the `tool_use` side. It takes an
    odd-length turn to move it, and the realistic source of one is a turn that
    ended after the tool result without a closing assistant message — an
    interrupt, which #7 made survivable rather than fatal.
    """
    out: list[dict] = [{"role": "user", "content": f"lead {i}"} for i in range(lead)]
    for n in range(pairs):
        out.append({"role": "user", "content": f"ask {n}"})
        out.extend(_pair(n))
    return out


def test_orphans_finds_a_result_without_its_use():
    history = _pair(0)
    assert _orphans(history) == set()
    assert _orphans(history[1:]) == {"tu0"}


def test_a_blind_tail_slice_would_have_orphaned(monkeypatch):
    """Pins the defect itself: the old boundary lands mid-pair."""
    history = _tool_heavy(_MAX_TURNS + 5)
    blind = history[-(_MAX_TURNS * 2):]
    # The old code kept exactly this slice. If it is clean the fixture is wrong
    # and the rest of this file proves nothing.
    assert _orphans(blind), "fixture must reproduce the orphaning boundary"


def test_compaction_leaves_no_orphans():
    history = _tool_heavy(_MAX_TURNS + 5)
    kept, compacted = _compact(history)
    assert compacted
    assert _orphans(kept) == set()


def test_compaction_keeps_the_notice():
    history = _tool_heavy(_MAX_TURNS + 5)
    kept, _ = _compact(history)
    assert "compacted" in kept[0]["content"]
    assert kept[0]["role"] == "user"


def test_no_orphans_across_a_range_of_boundaries():
    """The boundary must be safe wherever the pair structure puts it."""
    for lead in range(4):
        for extra in range(1, 12):
            history = _tool_heavy(_MAX_TURNS + extra, lead=lead)
            kept, _ = _compact(history)
            assert _orphans(kept) == set(), f"orphan at lead={lead} extra={extra}"


def test_complete_four_message_turns_are_handled():
    history: list[dict] = []
    for n in range(_MAX_TURNS + 6):
        history.append({"role": "user", "content": f"ask {n}"})
        history.extend(_pair(n))
        history.append({"role": "assistant", "content": f"answer {n}"})
    kept, _ = _compact(history)
    assert _orphans(kept) == set()


def test_compaction_is_idempotent():
    history = _tool_heavy(_MAX_TURNS + 5)
    once, _ = _compact(history)
    twice, again = _compact(once)
    assert _orphans(twice) == set()
    if again:
        assert _orphans(twice) == set()


def test_short_history_is_untouched():
    history = _pair(0) + _pair(1)
    kept, compacted = _compact(history)
    assert not compacted
    assert kept is history


def test_safe_start_walks_back_to_a_clean_boundary():
    history = _pair(0) + _pair(1) + _pair(2)
    # index 1 is mid-pair: the tool_result at 1 answers the tool_use at 0.
    assert _safe_start(history, 1) == 0


def test_plain_string_history_still_compacts():
    history = [{"role": "user", "content": f"m{i}"} for i in range(_MAX_TURNS * 2 + 6)]
    kept, compacted = _compact(history)
    assert compacted
    assert len(kept) <= _MAX_TURNS * 2 + 1


def test_the_budget_counts_the_system_prompt_and_tool_schemas():
    """_MAX_CHARS counted history alone; with MCP the schemas are the big half."""
    import argparse

    from ratatosk.crown import _MAX_CHARS, RuntimeState

    class _W:
        session_id = "s"
        cwd = "."
        path = "p"

    history = [{"role": "user", "content": "x" * 100} for _ in range(10)]
    assert _compact(history)[1] is False

    fat = RuntimeState(
        args=argparse.Namespace(local=True, trust=False, mcp=True, listen=False, deposit=False),
        model="m",
        writer=_W(),
        history=history,
        system_prompt="s" * (_MAX_CHARS // 2),
        all_tools=[{"name": "t", "blob": "b" * (_MAX_CHARS // 2)}],
        mcp_names=set(),
        mcp_call=None,
        client=None,
        policy=None,
        hooks=None,
    )
    # Same history, but the overhead alone now exceeds the budget, so it must
    # trim even though the turn cap is nowhere near.
    kept, compacted = _compact(history, fat)
    assert compacted is True
    assert len(kept) < len(history) + 1
