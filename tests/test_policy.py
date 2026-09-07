from ratatosk.policy import PolicyStore


def test_policy_set_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    store = PolicyStore()
    assert store.decide("Bash") == "confirm"

    store.set_rule("Read", "allow")
    assert store.decide("Read") == "allow"

    store.set_rule("Danger*", "deny")
    assert store.decide("DangerousTool") == "deny"

    assert store.remove_rule("Read") is True
    assert store.decide("Read") == "confirm"
