"""ratatosk.wake_policy — the role-default WAKE POLICY table.

Sealed 2026-09-22, pair 3566adb5. These tests cover load()/validation only;
enter()'s resolution and run_wake()'s enforcement are covered in
test_seat.py and test_daemon.py respectively.
"""

import json

import pytest

from ratatosk import wake_policy as wp


def test_load_reads_the_bundled_table():
    table = wp.load()
    assert "auditor" in table and "build-work-order" in table
    auditor = table["auditor"]
    assert auditor.role == "auditor"
    assert auditor.permits("Read")
    assert not auditor.permits("Write") and not auditor.permits("Edit")
    assert auditor.write_scope is None
    assert auditor.source == "role_default"


def test_no_bundled_role_ever_grants_bash():
    table = wp.load()
    assert all("Bash" not in policy.allow for policy in table.values())


def test_build_role_has_write_edit_scoped_to_the_worktree():
    table = wp.load()
    builder = table["build-work-order"]
    assert builder.permits("Write") and builder.permits("Edit")
    assert builder.write_scope == wp.WRITE_SCOPE_WORKTREE


def test_resolve_for_role_refuses_an_unknown_role():
    table = wp.load()
    with pytest.raises(wp.WakePolicyError, match="no wake policy for role 'nobody'"):
        wp.resolve_for_role(table, "nobody")


def test_resolve_for_role_returns_the_named_entry():
    table = wp.load()
    assert wp.resolve_for_role(table, "witness").role == "witness"


def test_load_refuses_a_table_that_names_bash(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {"roles": {"rogue": {"allow": ["Read", "Bash"], "write_scope": None}}}
        )
    )
    with pytest.raises(wp.WakePolicyError, match="forbidden tool"):
        wp.load(bad)


def test_load_refuses_a_bad_write_scope(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"roles": {"x": {"allow": ["Read"], "write_scope": "anywhere"}}})
    )
    with pytest.raises(wp.WakePolicyError, match="write_scope"):
        wp.load(bad)


def test_load_refuses_a_non_list_allow(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"roles": {"x": {"allow": "Read", "write_scope": None}}}))
    with pytest.raises(wp.WakePolicyError, match="'allow' must be a list"):
        wp.load(bad)


def test_load_refuses_empty_roles(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"roles": {}}))
    with pytest.raises(wp.WakePolicyError, match="non-empty"):
        wp.load(bad)


def test_load_refuses_missing_file(tmp_path):
    with pytest.raises(wp.WakePolicyError, match="cannot read"):
        wp.load(tmp_path / "does-not-exist.json")


def test_load_refuses_bad_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(wp.WakePolicyError, match="not valid JSON"):
        wp.load(bad)


def test_from_manifest_builds_a_policy_stamped_manifest_source():
    policy = wp.from_manifest(
        {"allow": ["Read", "Write"], "write_scope": "packet_worktree"}, role="builder"
    )
    assert policy.source == "manifest"
    assert policy.permits("Write") and policy.write_scope == wp.WRITE_SCOPE_WORKTREE


def test_from_manifest_still_refuses_bash():
    with pytest.raises(wp.WakePolicyError, match="forbidden"):
        wp.from_manifest({"allow": ["Bash"], "write_scope": None}, role="rogue")


def test_as_dict_carries_role_allow_write_scope_and_source():
    policy = wp.WakePolicy(role="auditor", allow=frozenset({"Read"}), write_scope=None)
    assert policy.as_dict() == {
        "role": "auditor",
        "allow": ["Read"],
        "write_scope": None,
        "source": "role_default",
    }
