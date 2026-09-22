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


def willow_mcp_python_named_in_unset_environment(text: str) -> list[str]:
    """Offender list: any UnsetEnvironment= line that also names
    WILLOW_MCP_PYTHON — setting it explicitly and unsetting it in the same
    file would be self-defeating."""
    offenders = []
    for line in text.splitlines():
        if (
            line.startswith("UnsetEnvironment=")
            and "WILLOW_MCP_PYTHON" in line.split("=", 1)[1].split()
        ):
            offenders.append(line)
    return offenders


_FILL = {
    "PYTHON": "/opt/willow-mcp/.venv/bin/python3",
    "HOME": "/home/op",
    "WILLOW_HOME": "/home/op/.willow",
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


def test_template_has_no_unresolved_placeholders_once_the_fillable_set_is_given():
    """render_values() only ever fills PYTHON, UNIT, WILLOW_HOME,
    WILLOW_STORE_ROOT, PG_DB, USER, HOME, XDG_CONFIG_HOME (+ optional
    WILLOW_KEYRING/WILLOW_PGP_FINGERPRINT/NESTOR_DB). Anything this
    template asks for beyond that set is ETEMPLATE at install time — this
    template only ever asks for @HOME@/@WILLOW_HOME@/@PYTHON@, all in that
    fillable set."""
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
