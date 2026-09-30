"""A seat asks the broker only for the tools its manifest grants.

Gap 565d2f8251fe: the broker lists every registered tool to every caller, and
a listener that sent that whole list logged ~30 ``gate: 'loki' denied tool``
lines per start. The grant is read from the broker's own ``whoami``
(``tools_allowed``), never from a list kept in ratatosk.
"""

import json

from ratatosk import mcp_client


def _tools(*names):
    return [{"name": n, "description": "", "input_schema": {}} for n in names]


def _whoami(allowed):
    def call(tool, inputs):
        assert tool == "whoami"
        assert inputs == {"app_id": "loki"}
        return json.dumps({"app_id": "loki", "tools_allowed": allowed})

    return call


def test_a_manifest_without_unit_status_yields_a_list_without_it():
    listed = _tools("store_get", "unit_status", "kb_journal", "gap_touching")
    names = {t["name"] for t in listed}

    tools, kept, note = mcp_client.granted_only(
        listed, names, _whoami(["store_get", "kb_journal"]), "loki"
    )

    assert [t["name"] for t in tools] == ["store_get", "kb_journal"]
    assert kept == {"store_get", "kb_journal"}
    assert "unit_status" not in kept and "gap_touching" not in kept
    assert "granted 2 of 4" in note


def test_no_denied_tool_is_requested():
    listed = _tools("a", "b", "c", "d", "e")
    names = {t["name"] for t in listed}
    granted = ["b", "d"]

    tools, kept, _ = mcp_client.granted_only(listed, names, _whoami(granted), "loki")

    assert {t["name"] for t in tools} <= set(granted) | {"whoami"}
    assert kept <= set(granted) | {"whoami"}


def test_whoami_is_never_filtered_out():
    listed = _tools("whoami", "store_get")
    names = {"whoami", "store_get"}

    tools, kept, _ = mcp_client.granted_only(listed, names, _whoami([]), "loki")

    assert [t["name"] for t in tools] == ["whoami"]
    assert kept == {"whoami"}


def test_an_unreadable_grant_leaves_the_list_whole_and_says_so():
    listed = _tools("store_get", "unit_status")
    names = {"store_get", "unit_status"}

    for answer in (
        "[mcp-error] boom",
        json.dumps({"error": "no_app_id"}),
        json.dumps({"app_id": "loki"}),
        json.dumps({"tools_allowed": "store_get"}),
    ):
        tools, kept, note = mcp_client.granted_only(
            listed, names, lambda t, i, a=answer: a, "loki"
        )
        assert tools == listed and kept == names
        assert "listing unfiltered" in note


def test_a_raising_transport_leaves_the_list_whole():
    listed = _tools("store_get")

    def boom(tool, inputs):
        raise RuntimeError("down")

    tools, kept, note = mcp_client.granted_only(listed, {"store_get"}, boom, "loki")

    assert tools == listed and kept == {"store_get"}
    assert "whoami raised" in note


def test_the_daemon_hands_its_seat_only_the_granted_tools(
    monkeypatch, capsys, tmp_path
):
    """ratatosk-listen --app-id loki: the SeatDaemon is built with the
    narrowed list, so the wake never sends (or asks for) a denied tool."""
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "loki")
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)
    from ratatosk import daemon as _daemon_mod

    listed = _tools("store_get", "unit_status", "kb_journal")
    monkeypatch.setattr(
        mcp_client, "start", lambda: (listed, {t["name"] for t in listed})
    )

    def call(tool, inputs):
        if tool == "whoami":
            return json.dumps({"tools_allowed": ["store_get", "kb_journal"]})
        return {}

    monkeypatch.setattr(mcp_client, "call", call)
    monkeypatch.setattr(mcp_client, "shutdown", lambda *a, **kw: True)

    built = {}
    real = _daemon_mod.SeatDaemon

    class Spy(real):
        def __init__(self, **kw):
            built.update(kw)
            super().__init__(**kw)

        def run_forever(self, on_status=None):
            return None

    monkeypatch.setattr(_daemon_mod, "SeatDaemon", Spy)

    _daemon_mod.main(["--app-id", "loki"])

    assert [t["name"] for t in built["mcp_extra_tools"]] == ["store_get", "kb_journal"]
    assert built["mcp_names"] == {"store_get", "kb_journal"}
    assert "granted 2 of 3" in capsys.readouterr().out
