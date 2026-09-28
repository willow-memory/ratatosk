"""Render checks for deploy/*.service.template files.

ratatosk's own venv does not have willow_mcp installed (that is the whole
subject of Loki B4415DF9 — see ratatosk/mcp_client.py's
``default_mcp_argv``), so these tests cannot import
``unit_install_executor.render_template`` and instead reproduce its
``@KEY@``-substitution rule (unit_install_executor.py: ``_PLACEHOLDER_RE =
re.compile(r"@([A-Z][A-Z0-9_]*)@")``, whole-text replace) as plain data —
enough to prove what a real render would produce without a cross-repo
import.
"""

import re
from pathlib import Path

TEMPLATE = (
    Path(__file__).parent.parent / "deploy" / "ratatosk-listen-loki.service.template"
)

_PLACEHOLDER_RE = re.compile(r"@([A-Z][A-Z0-9_]*)@")


def render(text: str, values: dict[str, str]) -> str:
    rendered = text
    for key, value in values.items():
        rendered = rendered.replace(f"@{key}@", value)
    return rendered


def missing_willow_mcp_python_line(text: str) -> list[str]:
    """Offender list (empty means clean): the exact Environment= DIRECTIVE
    line that sets WILLOW_MCP_PYTHON from @PYTHON@ is absent from
    ``text``'s own lines. Loki B4415DF9: default_mcp_argv() reads
    WILLOW_MCP_PYTHON correctly, but nothing supplied it — the template
    only refrained from unsetting a key that was never set. Matched as a
    whole LINE, not a bare substring — the header comment quotes this same
    text in backticks as documentation, and a substring check would find
    that mention even with the real directive removed. This is the scan;
    the tests below plant it."""
    line = "Environment=WILLOW_MCP_PYTHON=@PYTHON@"
    return [] if line in text.splitlines() else [line]


def unresolved_placeholders_after_render(
    text: str, values: dict[str, str]
) -> list[str]:
    """Offender list: placeholder names still present after rendering with
    ``values`` — what render_template itself refuses ETEMPLATE on."""
    rendered = render(text, values)
    return sorted(set(_PLACEHOLDER_RE.findall(rendered)))


def missing_explicit_env_line(text: str, name: str, placeholder: str) -> list[str]:
    """Offender list (empty means clean): the exact Environment= DIRECTIVE
    line setting ``name`` from ``@placeholder@`` is absent from ``text``'s
    own lines. Same shape as ``missing_willow_mcp_python_line`` above,
    generalized: an env-file key this daemon and its spawned willow-mcp
    child both need must never depend on the env FILE happening to define
    it — env-unit.install-amend-5A1FEB52 (the WILLOW_HOME defect: the file's
    own EnvironmentFile= line only uses WILLOW_HOME to locate the file, not
    to guarantee the file defines it)."""
    line = f"Environment={name}=@{placeholder}@"
    return [] if line in text.splitlines() else [line]


def unset_environment_names(text: str) -> set[str]:
    """Every key named across every UnsetEnvironment= line, unioned."""
    names: set[str] = set()
    for line in text.splitlines():
        if line.startswith("UnsetEnvironment="):
            names.update(line.split("=", 1)[1].split())
    return names


def named_in_unset_environment(text: str, name: str) -> list[str]:
    """Offender list: any UnsetEnvironment= line that also names ``name`` —
    setting it explicitly and unsetting it in the same file would be
    self-defeating."""
    return [
        line
        for line in text.splitlines()
        if line.startswith("UnsetEnvironment=") and name in line.split("=", 1)[1].split()
    ]


def willow_mcp_python_named_in_unset_environment(text: str) -> list[str]:
    """Offender list: any UnsetEnvironment= line that also names
    WILLOW_MCP_PYTHON — setting it explicitly and unsetting it in the same
    file would be self-defeating."""
    return named_in_unset_environment(text, "WILLOW_MCP_PYTHON")


_FILL = {
    "PYTHON": "/opt/willow-mcp/.venv/bin/python3",
    "HOME": "/home/op",
    "WILLOW_HOME": "/home/op/.willow",
    "WILLOW_STORE_ROOT": "/home/op/.willow/store",
}


def test_template_sets_willow_mcp_python_from_the_python_placeholder():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert missing_willow_mcp_python_line(text) == []
    rendered = render(text, _FILL)
    assert "Environment=WILLOW_MCP_PYTHON=/opt/willow-mcp/.venv/bin/python3" in rendered


def test_plant_missing_willow_mcp_python_line_is_caught():
    """Fires the scan on a text that lacks the line — proves it would have
    caught the exact bug Loki B4415DF9 found (the line simply absent)."""
    stripped = TEMPLATE.read_text(encoding="utf-8").replace(
        "Environment=WILLOW_MCP_PYTHON=@PYTHON@\n", ""
    )
    assert missing_willow_mcp_python_line(stripped) == [
        "Environment=WILLOW_MCP_PYTHON=@PYTHON@"
    ]


def test_willow_mcp_python_is_not_also_named_in_unset_environment():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert willow_mcp_python_named_in_unset_environment(text) == []


def test_plant_unset_environment_naming_willow_mcp_python_is_caught():
    """Fires the scan on a text where UnsetEnvironment= was (wrongly) also
    given WILLOW_MCP_PYTHON — self-defeating alongside the explicit set."""
    poisoned = TEMPLATE.read_text(encoding="utf-8").replace(
        "UnsetEnvironment=CEREBRAS_API_KEY",
        "UnsetEnvironment=WILLOW_MCP_PYTHON CEREBRAS_API_KEY",
    )
    offenders = willow_mcp_python_named_in_unset_environment(poisoned)
    assert offenders and "WILLOW_MCP_PYTHON" in offenders[0]


def test_template_sets_willow_home_explicitly_from_the_willow_home_placeholder():
    """An egress lease is not an entry gate (fix/lease-is-not-an-entry-blocker
    / fix/refuse-only-entry-blockers), but this defect is upstream of that
    split entirely: a listener with no explicit `Environment=WILLOW_HOME=`
    line let its own WILLOW_HOME (and the willow-mcp child's) fall back to
    whatever `ratatosk.paths` and the spawned broker default to when unset —
    read as `no_egress_lease`/`manifest_unreadable` from a stale home, which
    looked like a network problem and was actually an environment one."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert missing_explicit_env_line(text, "WILLOW_HOME", "WILLOW_HOME") == []
    rendered = render(text, _FILL)
    assert "Environment=WILLOW_HOME=/home/op/.willow" in rendered


def test_plant_missing_willow_home_line_is_caught():
    stripped = TEMPLATE.read_text(encoding="utf-8").replace(
        "Environment=WILLOW_HOME=@WILLOW_HOME@\n", ""
    )
    assert missing_explicit_env_line(stripped, "WILLOW_HOME", "WILLOW_HOME") == [
        "Environment=WILLOW_HOME=@WILLOW_HOME@"
    ]


def test_template_sets_willow_store_root_explicitly():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert missing_explicit_env_line(text, "WILLOW_STORE_ROOT", "WILLOW_STORE_ROOT") == []
    rendered = render(text, _FILL)
    assert "Environment=WILLOW_STORE_ROOT=/home/op/.willow/store" in rendered


def test_plant_missing_willow_store_root_line_is_caught():
    stripped = TEMPLATE.read_text(encoding="utf-8").replace(
        "Environment=WILLOW_STORE_ROOT=@WILLOW_STORE_ROOT@\n", ""
    )
    assert missing_explicit_env_line(
        stripped, "WILLOW_STORE_ROOT", "WILLOW_STORE_ROOT"
    ) == ["Environment=WILLOW_STORE_ROOT=@WILLOW_STORE_ROOT@"]


def test_willow_store_root_is_not_also_named_in_unset_environment():
    """Setting it explicitly (above) and unsetting it in the same file would
    be self-defeating — the exact shape the WILLOW_MCP_PYTHON check already
    guards, generalized to this key."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert named_in_unset_environment(text, "WILLOW_STORE_ROOT") == []


def test_plant_unset_environment_naming_willow_store_root_is_caught():
    poisoned = TEMPLATE.read_text(encoding="utf-8").replace(
        "UnsetEnvironment=CEREBRAS_API_KEY",
        "UnsetEnvironment=WILLOW_STORE_ROOT CEREBRAS_API_KEY",
    )
    offenders = named_in_unset_environment(poisoned, "WILLOW_STORE_ROOT")
    assert offenders and "WILLOW_STORE_ROOT" in offenders[0]


def test_template_has_no_unresolved_placeholders_once_the_fillable_set_is_given():
    """render_values() only ever fills PYTHON, UNIT, WILLOW_HOME,
    WILLOW_STORE_ROOT, PG_DB, USER, HOME, XDG_CONFIG_HOME (+ optional
    WILLOW_KEYRING/WILLOW_PGP_FINGERPRINT/NESTOR_DB). Anything this
    template asks for beyond that set is ETEMPLATE at install time — this
    template only ever asks for
    @HOME@/@WILLOW_HOME@/@WILLOW_STORE_ROOT@/@PYTHON@, all in that fillable
    set (confirmed directly against unit_install_executor.render_values()'s
    own key list, not copied from memory — see the packet's read path)."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert unresolved_placeholders_after_render(text, _FILL) == []


def test_plant_an_unfillable_placeholder_is_caught():
    """Fires the scan by naming a placeholder render_values() never
    fills — proves an ETEMPLATE-shaped regression would be caught."""
    poisoned = TEMPLATE.read_text(encoding="utf-8") + "\n# @NOT_A_REAL_KEY@\n"
    left = unresolved_placeholders_after_render(poisoned, _FILL)
    assert left == ["NOT_A_REAL_KEY"]


def test_template_declares_its_own_concrete_unit_name():
    text = TEMPLATE.read_text(encoding="utf-8")
    first_line = text.splitlines()[0]
    assert first_line == "# unit: ratatosk-listen-loki.service"
