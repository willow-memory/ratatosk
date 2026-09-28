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


def exec_start_line(text: str) -> str:
    """The one ``ExecStart=`` line, or ``""`` if the template has none."""
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            return line
    return ""


def missing_execstart_env_pin(text: str, name: str, placeholder: str) -> list[str]:
    """Offender list (empty means clean): ``ExecStart=`` does not pin ``name``
    via ``env(1)`` from ``@placeholder@`` as one of its leading assignments.

    Loki 1317FF7D B2: a plain ``Environment=NAME=@PLACEHOLDER@`` directive
    does NOT win over a same-named key in ``$WILLOW_HOME/env`` — systemd
    gives ``EnvironmentFile=`` precedence over ``Environment=`` regardless of
    line order. ``env(1)`` run as the ExecStart command itself is the one
    place a pin genuinely cannot be shadowed by the env file: it runs AFTER
    systemd has already assembled the environment from
    ``EnvironmentFile=``/``Environment=``/``UnsetEnvironment=``, and its own
    assignments override whatever it inherits before it execs the real
    command in turn."""
    token = f"{name}=@{placeholder}@"
    line = exec_start_line(text)
    if not line.startswith("ExecStart=/usr/bin/env "):
        return [f"ExecStart= does not run via /usr/bin/env: {line!r}"]
    assignments = line[len("ExecStart=/usr/bin/env ") :].split()
    return [] if token in assignments else [token]


_WILLOW_HOME_ASSIGNMENT_RE = re.compile(r"WILLOW_HOME=(\S+)")


def hardcoded_home_lines(text: str) -> list[str]:
    """Offender list: any non-comment, non-blank line assigning
    ``WILLOW_HOME=`` to anything other than the exact placeholder token
    ``@WILLOW_HOME@`` — a hardcoded absolute path baked in instead of the
    placeholder (Loki 1317FF7D M-R7), whether it is its own
    ``Environment=WILLOW_HOME=...`` directive (a second, literal assignment
    appended after the legitimate placeholder line) or an ``env(1)``
    assignment embedded inside ``ExecStart=``."""
    offenders = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for match in _WILLOW_HOME_ASSIGNMENT_RE.finditer(stripped):
            if match.group(1) != "@WILLOW_HOME@":
                offenders.append(line)
                break
    return offenders


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
        if line.startswith("UnsetEnvironment=")
        and name in line.split("=", 1)[1].split()
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


def test_template_pins_willow_home_via_execstart_env():
    """Loki 1317FF7D B2: a plain `Environment=WILLOW_HOME=` directive does
    NOT win over a same-named key in $WILLOW_HOME/env (systemd gives
    EnvironmentFile= precedence over Environment=, whatever the line
    order) — so the pin has to live in ExecStart itself, via env(1), which
    runs after systemd has already assembled the environment. Upstream
    defect this exists to close (fix/lease-is-not-an-entry-blocker /
    fix/refuse-only-entry-blockers): a listener with no real WILLOW_HOME pin
    let its own WILLOW_HOME (and the willow-mcp child's) fall back to
    whatever `ratatosk.paths` and the spawned broker default to when unset —
    read as `no_egress_lease`/`manifest_unreadable` from a stale home, which
    looked like a network problem and was actually an environment one."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert missing_execstart_env_pin(text, "WILLOW_HOME", "WILLOW_HOME") == []
    rendered = render(text, _FILL)
    assert "ExecStart=/usr/bin/env WILLOW_HOME=/home/op/.willow " in rendered


def test_plant_missing_willow_home_execstart_pin_is_caught():
    stripped = TEMPLATE.read_text(encoding="utf-8").replace(
        "ExecStart=/usr/bin/env WILLOW_HOME=@WILLOW_HOME@ WILLOW_STORE_ROOT=@WILLOW_STORE_ROOT@ ",
        "ExecStart=/usr/bin/env WILLOW_STORE_ROOT=@WILLOW_STORE_ROOT@ ",
    )
    assert missing_execstart_env_pin(stripped, "WILLOW_HOME", "WILLOW_HOME") == [
        "WILLOW_HOME=@WILLOW_HOME@"
    ]


def test_template_pins_willow_store_root_via_execstart_env():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert (
        missing_execstart_env_pin(text, "WILLOW_STORE_ROOT", "WILLOW_STORE_ROOT") == []
    )
    rendered = render(text, _FILL)
    assert "WILLOW_STORE_ROOT=/home/op/.willow/store" in rendered


def test_plant_missing_willow_store_root_execstart_pin_is_caught():
    stripped = TEMPLATE.read_text(encoding="utf-8").replace(
        " WILLOW_STORE_ROOT=@WILLOW_STORE_ROOT@ @HOME@",
        " @HOME@",
    )
    assert missing_execstart_env_pin(
        stripped, "WILLOW_STORE_ROOT", "WILLOW_STORE_ROOT"
    ) == ["WILLOW_STORE_ROOT=@WILLOW_STORE_ROOT@"]


def test_execstart_does_not_run_via_a_plain_environment_directive():
    """The whole point of the env(1) pin is that it is NOT a plain
    Environment= directive (those lose to EnvironmentFile=) — pin the shape
    of the fix, not just the presence of the two keys."""
    text = TEMPLATE.read_text(encoding="utf-8")
    line = exec_start_line(text)
    assert line.startswith("ExecStart=/usr/bin/env WILLOW_HOME=@WILLOW_HOME@ ")


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


def test_template_has_no_hardcoded_home_path():
    """Loki 1317FF7D M-R7 (LOW test gap, 13 passed before this test existed):
    the only legitimate WILLOW_HOME= token anywhere in the template is the
    placeholder itself. A hardcoded absolute path — including a SECOND
    WILLOW_HOME= assignment appended after the real placeholder line/token —
    must be caught."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert hardcoded_home_lines(text) == []


def test_plant_a_hardcoded_home_line_is_caught():
    """Fires the scan on the exact M-R7 mutation shape: a literal
    Environment=WILLOW_HOME=/home/... line appended after the placeholder
    token, instead of relying on @WILLOW_HOME@."""
    poisoned = (
        TEMPLATE.read_text(encoding="utf-8")
        + "\nEnvironment=WILLOW_HOME=/home/sean-campbell/.willow\n"
    )
    offenders = hardcoded_home_lines(poisoned)
    assert offenders and "/home/sean-campbell/.willow" in offenders[0]


def test_plant_a_hardcoded_home_token_inside_execstart_is_caught():
    """The same mutation shape, but baked into ExecStart's env(1) call
    instead of a standalone Environment= line — hardcoded_home_lines must
    catch it there too, not only at line-start."""
    poisoned = TEMPLATE.read_text(encoding="utf-8").replace(
        "WILLOW_HOME=@WILLOW_HOME@",
        "WILLOW_HOME=/home/sean-campbell/.willow",
    )
    offenders = hardcoded_home_lines(poisoned)
    assert offenders and "/home/sean-campbell/.willow" in offenders[0]


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
