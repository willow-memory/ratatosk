from ratatosk.policy import PolicyRule, PolicyStore, shadowed_rules


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


def _rules(*pairs) -> list[PolicyRule]:
    return [PolicyRule(pattern=p, action=a) for p, a in pairs]


def test_a_catch_all_shadows_everything_after_it():
    rules = _rules(("*", "deny"), ("Read", "allow"), ("Bash", "confirm"))
    found = shadowed_rules(rules)
    assert [s.rule.pattern for s in found] == ["Read", "Bash"]
    assert all(s.by.pattern == "*" for s in found)


def test_a_broader_glob_shadows_a_narrower_one():
    rules = _rules(("Ba*", "deny"), ("Bash*", "allow"))
    found = shadowed_rules(rules)
    assert len(found) == 1
    assert found[0].rule.pattern == "Bash*"
    assert found[0].by_index == 0


def test_order_is_what_decides_not_specificity():
    """The narrow rule first is fine — nothing is unreachable."""
    assert shadowed_rules(_rules(("Bash", "confirm"), ("*", "deny"))) == []


def test_partial_overlap_is_not_reported():
    """`*h` and `Bash*` share members but neither eats the other. A false
    positive here would tell an operator to delete a live rule."""
    assert shadowed_rules(_rules(("*h", "deny"), ("Bash*", "allow"))) == []


def test_a_duplicate_pattern_is_shadowed():
    rules = _rules(("Write", "allow"), ("Write", "deny"))
    found = shadowed_rules(rules)
    assert len(found) == 1
    assert found[0].index == 1


def test_shadowed_reports_what_decide_actually_does(tmp_path, monkeypatch):
    """The report has to agree with the verdict, or it is just a second opinion."""
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    store = PolicyStore()
    store.save(_rules(("*", "deny"), ("Read", "allow")))

    assert store.decide("Read") == "deny", "the shadowing rule is the one that answers"
    shadowed = store.shadowed()
    assert [s.rule.pattern for s in shadowed] == ["Read"]
