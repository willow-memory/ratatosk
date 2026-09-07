from ratatosk.protocol.envelope import Intent, build_envelope, parse_grove_message, validate_envelope


def test_validate_rejects_wrong_node():
    env = build_envelope(to="other", prompt="hi", intent=Intent.CHAT.value)
    result = validate_envelope(env, node="ratatosk", check_replay=False)
    assert not result.ok


def test_parse_addressed_message():
    env = parse_grove_message({"sender": "willow", "content": "ratatosk: status please"})
    assert env is not None
    assert env.to == "ratatosk"
    assert "status" in env.prompt


def test_a_missing_nonce_is_rejected_not_skipped():
    """Replay protection used to be opt-out by omission: `check_replay and
    env.nonce` meant leaving the field off disabled the check entirely."""
    from ratatosk.protocol.envelope import build_envelope, validate_envelope

    env = build_envelope(to="ratatosk", prompt="hi", intent=Intent.CHAT.value)
    env.nonce = ""
    result = validate_envelope(env, node="ratatosk")
    assert not result.ok
    assert any("missing nonce" in e for e in result.errors)


def test_a_duplicate_nonce_is_still_caught():
    from ratatosk.protocol.envelope import build_envelope, clear_nonce_cache, validate_envelope

    clear_nonce_cache()
    env = build_envelope(to="ratatosk", prompt="hi", intent=Intent.CHAT.value)
    assert validate_envelope(env, node="ratatosk").ok
    again = validate_envelope(env, node="ratatosk")
    assert not again.ok
    assert any("replay" in e for e in again.errors)


def test_nonce_eviction_is_fifo_so_recent_nonces_survive():
    """`set.pop()` evicted an arbitrary element, so a nonce seen seconds ago
    could be dropped while stale ones stayed — and a dropped nonce is one that
    will be accepted a second time."""
    from ratatosk.protocol import envelope as env_mod

    env_mod.clear_nonce_cache()
    monkey_cap = 10
    original, env_mod._MAX_SEEN = env_mod._MAX_SEEN, monkey_cap
    try:
        for i in range(monkey_cap):
            assert env_mod._remember_nonce(f"n{i}")
        # One more evicts the oldest, and only the oldest.
        assert env_mod._remember_nonce("newest")
        assert "n0" not in env_mod._SEEN_NONCES, "oldest should have gone"
        for i in range(1, monkey_cap):
            assert f"n{i}" in env_mod._SEEN_NONCES, f"n{i} evicted out of order"
        assert "newest" in env_mod._SEEN_NONCES
    finally:
        env_mod._MAX_SEEN = original
        env_mod.clear_nonce_cache()


def test_the_cache_never_grows_past_the_cap():
    from ratatosk.protocol import envelope as env_mod

    env_mod.clear_nonce_cache()
    original, env_mod._MAX_SEEN = env_mod._MAX_SEEN, 5
    try:
        for i in range(50):
            env_mod._remember_nonce(f"x{i}")
        assert len(env_mod._SEEN_NONCES) == 5
    finally:
        env_mod._MAX_SEEN = original
        env_mod.clear_nonce_cache()
