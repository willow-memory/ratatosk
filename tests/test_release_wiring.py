"""The release chain is three files plus one repository setting, and every
disagreement between them is silent.

    release-please-config.json     decides the tag name and what cuts a release
    .release-please-manifest.json  is the version it bumps from
    .github/workflows/release.yml  fires on a tag pattern and publishes
    the branch ruleset               is what makes `--auto` actually wait

Nothing joins them up at runtime. A mismatch does not raise — it means a release
quietly happens wrong, and this repo has the scar: **1.2.2 through 1.2.6 were
published to PyPI before CI had run.** `main` carried no required status checks,
so GitHub refused `enablePullRequestAutoMerge`; `gh pr merge --auto` fell back to
merging immediately, and the arming step ended in `|| true`, so the workflow went
green while nothing was armed. Release PR #14 merged at 06:33:43 and its
test-matrix jobs started at 06:33:45.

Ported from kartikeya's file of the same name. Checks that do not apply here are
deliberately absent rather than copied: this repo has no changelog-rebuild step,
no release-body sync, and no second file storing a version. The two checks that
are *new* here — the arming step reporting its failure, and the required check
naming a job that exists — are this repo's own scar.

The pr-title checks at the bottom arrived with the workflow itself (fleet plan
Wave 2, G2-pr-title): because release-please.yml arms auto-merge, a PR *title*
of a release-cutting type would tag and publish unattended, and the file that
stops it is one this test asserts is present, wired to the packaged directory
pyproject actually names, and backed by a config whose hidden set is the
fleet's and whose reasoning is written beside it.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no tomllib, and this package still supports it
    import tomli as tomllib

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML needed to read the workflows")

_REPO = Path(__file__).resolve().parents[1]
_CONFIG = _REPO / "release-please-config.json"
_MANIFEST = _REPO / ".release-please-manifest.json"
_RELEASE_WF = _REPO / ".github" / "workflows" / "release.yml"
_RP_WF = _REPO / ".github" / "workflows" / "release-please.yml"
_TESTS_WF = _REPO / ".github" / "workflows" / "tests.yml"

#: The check the branch ruleset requires before a PR may merge. Named here so a
#: rename of the CI job is caught by a test rather than by a release.
REQUIRED_CHECK = "test"


def _json(p: Path) -> dict:
    return json.loads(p.read_text())


def _yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


def _package_config() -> dict:
    return _json(_CONFIG)["packages"]["."]


def _rp_steps() -> list[dict]:
    return _yaml(_RP_WF)["jobs"]["release-please"]["steps"]


def test_pyyaml_is_a_declared_dev_dependency():
    """This file importorskips on PyYAML, and a test that skips silently is the
    exact failure mode the rest of it exists to catch. If PyYAML is not in the
    dev extra, CI collects these tests, skips them all, and reports green."""
    pyproject = tomllib.loads((_REPO / "pyproject.toml").read_text())
    dev = pyproject["project"]["optional-dependencies"]["dev"]
    assert any(req.lower().startswith(("pyyaml", "yaml")) for req in dev), (
        f"PyYAML must be in the dev extra or this whole file skips unnoticed: {dev}"
    )


def test_the_tag_release_please_creates_matches_what_release_yml_listens_for():
    """With `include-component-in-tag` unset it defaults to *true* and the tag
    becomes `willow-ratatosk-vX.Y.Z`, which `v*` does not match — so the tag is
    created and nothing publishes, with no error anywhere. Observed on
    willow-mcp#256."""
    cfg = _package_config()
    version = _json(_MANIFEST)["."]
    tag = (
        f"{cfg['package-name']}-v{version}"
        if cfg.get("include-component-in-tag", True)
        else f"v{version}"
    )

    # `on:` parses as the boolean True — PyYAML applies the YAML 1.1 rule.
    patterns = list(_yaml(_RELEASE_WF)[True]["push"]["tags"])
    assert any(fnmatch.fnmatch(tag, p) for p in patterns), (
        f"release-please would create the tag {tag!r}, which matches none of "
        f"release.yml's trigger patterns {patterns!r}. Nothing would publish, "
        f"and nothing would report an error."
    )


def test_the_version_has_exactly_one_source():
    """The version lives in the tag; hatch-vcs derives it. A literal here is a
    second copy, and a second copy is what drifts."""
    pyproject = tomllib.loads((_REPO / "pyproject.toml").read_text())
    assert "version" in (pyproject["project"].get("dynamic") or [])
    assert "version" not in pyproject["project"], (
        "a literal project.version is a second source of truth"
    )
    assert pyproject["tool"]["hatch"]["version"]["source"] == "vcs"
    assert not _package_config().get("extra-files"), (
        "nothing in this repo stores a version, so nothing needs bumping"
    )


# A credential whose events actually trigger workflows. What is NOT acceptable is
# GITHUB_TOKEN, whose events GitHub suppresses — the release PR merges, no tag
# workflow fires, nothing publishes. jeles lost three releases to it.
NON_SUPPRESSED_CREDENTIALS = (
    "RELEASE_PLEASE_TOKEN",  # fine-grained PAT (being retired)
    "steps.app-token.outputs.token",  # willow-ci App installation token
)


def _names_a_non_suppressed_credential(value: object) -> bool:
    return any(c in str(value) for c in NON_SUPPRESSED_CREDENTIALS)


def test_the_credential_scan_catches_a_planted_github_token():
    """Planted: the exact value jeles ran on. `${{ secrets.GITHUB_TOKEN }}`
    names no non-suppressed credential, so the scan must say so; the
    willow-ci App token must clear it. Until this plant the scan below had
    never been shown to fire (G2-meta-scans)."""
    assert not _names_a_non_suppressed_credential("${{ secrets.GITHUB_TOKEN }}")
    assert _names_a_non_suppressed_credential("${{ steps.app-token.outputs.token }}")
    assert _names_a_non_suppressed_credential("${{ secrets.RELEASE_PLEASE_TOKEN }}")


def test_release_automation_uses_a_non_suppressed_credential_everywhere():
    used: set[str] = set()
    values: list[str] = []
    for step in _rp_steps():
        for value in list((step.get("env") or {}).values()) + list(
            (step.get("with") or {}).values()
        ):
            values.append(str(value))
            used.update(re.findall(r"secrets\.([A-Z_]+)", str(value)))
    assert any(_names_a_non_suppressed_credential(v) for v in values), (
        f"no non-suppressed credential anywhere in the job; secrets seen: {used}"
    )
    assert "GITHUB_TOKEN" not in used, (
        f"GITHUB_TOKEN's events do not trigger workflows; found {used}"
    )


def _arming_steps() -> list[dict]:
    return [s for s in _rp_steps() if "gh pr merge" in str(s.get("run", ""))]


def test_auto_merge_waits_for_ci_rather_than_merging_directly():
    """`--auto` is what makes the merge wait for the required checks. A plain
    merge publishes off an unverified commit — which is what happened here for
    five releases, because with no required checks there was nothing to wait for
    and `--auto` degraded to an immediate merge."""
    arming = _arming_steps()
    assert arming, "no step arms auto-merge on the release PR"
    for step in arming:
        for line in step["run"].splitlines():
            if "gh pr merge" in line and not line.strip().startswith("#"):
                assert "--auto" in line, f"merge without --auto: {line.strip()}"
                assert "--squash" not in line, (
                    "this fleet merges with merge commits; squash breaks release-please's parse"
                )


def test_the_arming_step_reports_a_failure_instead_of_swallowing_it():
    """This repo's own scar. The step ended in `|| true`, so when GitHub refused
    to arm auto-merge — `Protected branch rules not configured for this branch` —
    the workflow reported success and nobody knew for six releases.

    Failing the job would be wrong (a release PR that needs a manual merge is not
    a broken release), so the requirement is that it warns, the way the fleet's
    changelog-bail steps do."""
    for step in _arming_steps():
        run = step["run"]
        # Read the code, not the commentary. The step explains the `|| true` it
        # replaced, and a plain substring check would flag its own explanation —
        # the same trap kartikeya's pr-title test documents.
        code = "\n".join(
            line for line in run.splitlines() if not line.strip().startswith("#")
        )
        assert "|| true" not in code, (
            "`|| true` is what hid the unarmed auto-merge for six releases"
        )
        assert "::warning::" in code or "::error::" in code, (
            "a failure to arm must be annotated, not swallowed"
        )


def test_the_checkout_can_see_history_and_tags():
    checkout = next(
        s for s in _rp_steps() if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["fetch-depth"] == 0, "needs full history for the range"
    assert checkout["with"]["fetch-tags"] is True, (
        "needs tags to find the previous release"
    )


def test_the_required_check_names_a_job_that_actually_exists():
    """The branch ruleset requires the `test` check. If that job is renamed, the
    ruleset silently requires a check nothing produces and every PR blocks
    forever — the mirror image of requiring nothing, which is where this repo
    started."""
    jobs = _yaml(_TESTS_WF)["jobs"]
    assert REQUIRED_CHECK in jobs, (
        f"the ruleset requires the check {REQUIRED_CHECK!r}, absent from {sorted(jobs)}"
    )


def test_the_required_check_actually_gates_the_matrix():
    """`test` must fail when a matrix leg fails. A green aggregate job that does
    not depend on the matrix would satisfy the ruleset while proving nothing."""
    job = _yaml(_TESTS_WF)["jobs"][REQUIRED_CHECK]
    needs = job.get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    assert "test-matrix" in needs, (
        f"{REQUIRED_CHECK!r} must depend on test-matrix; needs={needs}"
    )
    body = " ".join(str(s.get("run", "")) + str(s.get("if", "")) for s in job["steps"])
    assert "test-matrix" in body and "exit 1" in body, (
        "the aggregate job must assert the matrix succeeded, not merely follow it"
    )


def test_this_package_is_past_1_0_so_the_pre_major_flags_are_inert():
    """Recorded rather than assumed, because the fleet's pin rule turns on it.

    Below 1.0, `bump-minor-pre-major: false` is what makes a consumer's `<1.0.0`
    a real compatibility range (fleet-versioning Rule 1). This package is already
    past 1.0, so those flags do nothing and a consumer's cap is simply the next
    major above the floor — `willow-ratatosk>=1.2.x,<2.0.0`.
    """
    version = _json(_MANIFEST)["."]
    major = int(version.split(".")[0])
    assert major >= 1, f"still pre-1.0 ({version}) — the pre-major flags matter again"
    cfg = _package_config()
    for flag in ("bump-minor-pre-major", "bump-patch-for-minor-pre-major"):
        assert cfg.get(flag) is False, (
            f"{flag} should stay false: inert now, correct if this ever forks a 0.x line"
        )


# ── the pr-title guard (G2-pr-title) ───────────────────────────────────────

_PR_TITLE_WF = ".github/workflows/pr-title.yml"

#: What arming looks like in release-please.yml. The guard is required
#: *because* of this line: without it the release PR waits for a human, who
#: sees the title before merging it.
_ARMS_AUTOMERGE = "gh pr merge --auto"

#: The types that must be hidden — the fleet's set, restated here only so a
#: disagreement with `tests/fleet_conventions.json` is a test failure in two
#: places rather than a silent drift in one.
_HIDDEN = frozenset({"chore", "ci", "docs", "test"})

_REQUIRED_COMMENTS = ("$comment-hidden-rule", "$comment-what-cuts-a-release")


def _arms_automerge(root: Path) -> bool:
    workflow = root / ".github" / "workflows" / "release-please.yml"
    return workflow.exists() and _ARMS_AUTOMERGE in workflow.read_text(encoding="utf-8")


def _guard_missing_when_armed(root: Path) -> bool:
    """True when this tree arms auto-merge on the release PR and carries no
    pr-title workflow — the exact state willow-mcp was in for v2.1.1."""
    return _arms_automerge(root) and not (root / _PR_TITLE_WF).exists()


def test_the_pr_title_guard_is_present_because_auto_merge_is_armed():
    assert _arms_automerge(_REPO), (
        "release-please.yml no longer arms auto-merge; re-read whether the guard is still required"
    )
    assert not _guard_missing_when_armed(_REPO), (
        f"{_PR_TITLE_WF} is missing: a release-cutting PR title would publish unattended"
    )


def _tree(tmp_path: Path, label: str, *, arms: bool, guard: bool) -> Path:
    root = tmp_path / label
    (root / ".github" / "workflows").mkdir(parents=True)
    line = f'{_ARMS_AUTOMERGE} --merge "$pr"' if arms else "gh pr list"
    (root / ".github" / "workflows" / "release-please.yml").write_text(
        f"jobs:\n  release-please:\n    steps:\n      - run: |\n          {line}\n",
        encoding="utf-8",
    )
    if guard:
        (root / _PR_TITLE_WF).write_text("name: PR title\n", encoding="utf-8")
    return root


def test_the_guard_check_fires_on_a_planted_tree_that_arms_without_the_guard(tmp_path):
    """Planted: three trees. Armed and unguarded must be reported; armed and
    guarded must not; unarmed and unguarded must not, because a hand-merged
    release PR is read by a human before it lands."""
    assert _guard_missing_when_armed(_tree(tmp_path, "bare", arms=True, guard=False))
    assert not _guard_missing_when_armed(
        _tree(tmp_path, "guarded", arms=True, guard=True)
    )
    assert not _guard_missing_when_armed(
        _tree(tmp_path, "manual", arms=False, guard=False)
    )


_PACKAGED_LINE = re.compile(r"^\s*PACKAGED\s*=\s*(\(.*\))\s*$", re.MULTILINE)


def _packaged_constant(workflow_text: str) -> tuple[str, ...]:
    """The `PACKAGED` tuple the pr-title workflow's inline script declares —
    the one per-repo constant in an otherwise fleet-identical file."""
    m = _PACKAGED_LINE.search(workflow_text)
    assert m, "pr-title.yml declares no PACKAGED constant"
    value = ast.literal_eval(m.group(1))
    assert isinstance(value, tuple) and all(isinstance(v, str) for v in value)
    return value


def _packaged_by_pyproject(pyproject_text: str) -> tuple[str, ...]:
    """What pyproject says is installable: each wheel package as a directory
    prefix, plus pyproject.toml itself, since a dependency change there alters
    the installed artifact."""
    packages = tomllib.loads(pyproject_text)["tool"]["hatch"]["build"]["targets"][
        "wheel"
    ]["packages"]
    return tuple(f"{pkg.rstrip('/')}/" for pkg in packages) + ("pyproject.toml",)


def _packaged_disagreement(workflow_text: str, pyproject_text: str) -> set[str]:
    """The symmetric difference between the two — empty when they agree."""
    return set(_packaged_constant(workflow_text)) ^ set(
        _packaged_by_pyproject(pyproject_text)
    )


def test_the_workflows_packaged_constant_agrees_with_pyproject():
    """The fleet's rule: PACKAGED is the one line that must not be copied
    between repos. This is the test the workflow's own comment promises."""
    disagreement = _packaged_disagreement(
        (_REPO / _PR_TITLE_WF).read_text(encoding="utf-8"),
        (_REPO / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert not disagreement, (
        f"pr-title.yml's PACKAGED and pyproject's wheel packages disagree on: {sorted(disagreement)}"
    )


def test_the_packaged_check_catches_a_planted_constant_copied_from_kartikeya():
    """Planted: the workflow as it would be if copied without editing the
    constant — kartikeya's `src/kartikeya/` against this repo's pyproject."""
    copied = '          PACKAGED = ("src/kartikeya/", "pyproject.toml")\n'
    pyproject = '[tool.hatch.build.targets.wheel]\npackages = ["ratatosk"]\n'
    assert _packaged_disagreement(copied, pyproject) == {"src/kartikeya/", "ratatosk/"}
    ours = '          PACKAGED = ("ratatosk/", "pyproject.toml")\n'
    assert _packaged_disagreement(ours, pyproject) == set()


def _hidden_types(config_text: str) -> frozenset[str]:
    sections = json.loads(config_text)["packages"]["."]["changelog-sections"]
    return frozenset(s["type"] for s in sections if s.get("hidden"))


def _missing_comments(config_text: str) -> list[str]:
    package = json.loads(config_text)["packages"]["."]
    return [c for c in _REQUIRED_COMMENTS if c not in package]


def test_the_hidden_set_is_the_fleets_and_its_reasoning_is_beside_it():
    text = _CONFIG.read_text(encoding="utf-8")
    assert _hidden_types(text) == _HIDDEN, (
        f"hidden set drifted from the fleet's {sorted(_HIDDEN)}: {sorted(_hidden_types(text))}"
    )
    assert _missing_comments(text) == [], (
        "the hidden set may not be edited without reading why it is what it is"
    )


def test_the_hidden_set_check_catches_a_planted_config_that_unhides_ci():
    """Planted: `ci` without `hidden: true`, and the reasoning comment gone —
    jeles v0.4.1, as a config file."""
    planted = json.dumps(
        {
            "packages": {
                ".": {
                    "changelog-sections": [
                        {"type": "feat", "section": "Added"},
                        {"type": "docs", "section": "Docs", "hidden": True},
                        {"type": "test", "section": "Tests", "hidden": True},
                        {"type": "ci", "section": "CI"},
                        {"type": "chore", "section": "Chores", "hidden": True},
                    ],
                    "$comment-what-cuts-a-release": "kept",
                }
            }
        }
    )
    assert _hidden_types(planted) == {"chore", "docs", "test"}
    assert _missing_comments(planted) == ["$comment-hidden-rule"]


# ── the trailer gate (E3-trailers) ─────────────────────────────────────────

_PILE = "docs/ideas.md"
_TRAILERS_WF = ".github/workflows/trailers.yml"


def _gate_missing_for_pile(root: Path) -> bool:
    """True when this tree carries a numbered idea pile and no workflow runs
    `reconciler verify` over it. A pile without the gate means an `Idea-Id`
    trailer can name an item the doc does not contain and nothing notices —
    and rule 2a asserts LANDED from that trailer ahead of every other signal."""
    return (root / _PILE).exists() and not (root / _TRAILERS_WF).exists()


def test_the_trailer_gate_is_present_because_a_pile_exists():
    assert (_REPO / _PILE).exists(), f"{_PILE} is the pile this repo keeps"
    assert not _gate_missing_for_pile(_REPO), (
        f"{_TRAILERS_WF} is missing: a dangling Idea-Id trailer would go unverified"
    )
    assert _workflow_verifies_the_pile(
        (_REPO / _TRAILERS_WF).read_text(encoding="utf-8")
    ), "the workflow must verify the pile this repo actually keeps"


def _workflow_verifies_the_pile(workflow_text: str) -> bool:
    """Does the workflow run `reconciler verify` over this repo's pile, by
    the path spelling that resolves under willow-reconciler 0.6.0?"""
    return f"reconciler verify --repo ./ --doc {_PILE}" in workflow_text


def test_the_workflow_check_catches_a_planted_workflow_verifying_another_pile():
    """Planted: a trailers workflow copied from a sibling that keeps its pile
    elsewhere, and one using the bare `--repo .` that does not resolve."""
    assert not _workflow_verifies_the_pile(
        "run: reconciler verify --repo ./ --doc docs/IDEAS.md\n"
    )
    assert not _workflow_verifies_the_pile(
        "run: reconciler verify --repo . --doc docs/ideas.md\n"
    )
    assert _workflow_verifies_the_pile(
        "run: reconciler verify --repo ./ --doc docs/ideas.md\n"
    )


def _pile_tree(tmp_path: Path, label: str, *, pile: bool, gate: bool) -> Path:
    root = tmp_path / label
    (root / ".github" / "workflows").mkdir(parents=True)
    if pile:
        (root / "docs").mkdir()
        (root / _PILE).write_text("1. an idea\n", encoding="utf-8")
    if gate:
        (root / _TRAILERS_WF).write_text("name: Trailers\n", encoding="utf-8")
    return root


def test_the_trailer_gate_check_fires_on_a_planted_tree_with_a_pile_and_no_gate(
    tmp_path,
):
    """Planted: a pile and no gate must be reported; a pile with the gate must
    not; no pile at all must not, since there is nothing to verify."""
    assert _gate_missing_for_pile(
        _pile_tree(tmp_path, "ungated", pile=True, gate=False)
    )
    assert not _gate_missing_for_pile(
        _pile_tree(tmp_path, "gated", pile=True, gate=True)
    )
    assert not _gate_missing_for_pile(
        _pile_tree(tmp_path, "nopile", pile=False, gate=False)
    )


# ── every job carries an effective permissions grant ───────────────────────

_WORKFLOWS_DIR = _REPO / ".github" / "workflows"


def _jobs_without_permissions(workflow_text: str) -> list[str]:
    """The jobs in one workflow that no `permissions:` block covers — neither
    a workflow-level block nor one on the job itself. Without one, the
    GITHUB_TOKEN gets the repository default, which is not read-only here;
    CodeQL raised exactly this on trailers.yml (#45), and the same shape was
    already in tests.yml and release.yml's build job."""
    doc = yaml.safe_load(workflow_text) or {}
    if "permissions" in doc:
        return []
    return [
        name
        for name, job in (doc.get("jobs") or {}).items()
        if "permissions" not in (job or {})
    ]


def test_every_workflow_job_has_an_effective_permissions_grant():
    offenders = {
        path.name: missing
        for path in sorted(_WORKFLOWS_DIR.glob("*.yml"))
        if (missing := _jobs_without_permissions(path.read_text(encoding="utf-8")))
    }
    assert not offenders, (
        f"jobs whose GITHUB_TOKEN falls back to the repository default: {offenders}"
    )


def test_the_permissions_check_catches_a_planted_workflow_with_no_grant():
    """Planted: three shapes. No block anywhere must be reported; a
    workflow-level block must cover every job; a job-level block must cover
    its own job and leave the others reported."""
    bare = "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n  b:\n    runs-on: ubuntu-latest\n"
    assert _jobs_without_permissions(bare) == ["a", "b"]
    top = "on: push\npermissions:\n  contents: read\n" + bare[len("on: push\n") :]
    assert _jobs_without_permissions(top) == []
    per_job = (
        "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
        "    permissions:\n      id-token: write\n  b:\n    runs-on: ubuntu-latest\n"
    )
    assert _jobs_without_permissions(per_job) == ["b"]


# ── the fleet CI floor (decision 5, C4-tests-yml) ───────────────────────────

_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")
_RUFF_PIN = re.compile(r"pip install ruff==(\d+\.\d+\.\d+)\b")
_GATE_JOB = "test"


def _classifier_minors(pyproject_text: str) -> list[str]:
    """Every `Programming Language :: Python :: 3.X` classifier, in order.
    The Linux matrix is derived from these, and this is the derivation."""
    classifiers = tomllib.loads(pyproject_text)["project"].get("classifiers", [])
    return [m.group(1) for c in classifiers if (m := _CLASSIFIER.match(c))]


def _matrix_versions(workflow: dict, job: str) -> list[str]:
    return [
        str(v) for v in workflow["jobs"][job]["strategy"]["matrix"]["python-version"]
    ]


def _floor_and_ceiling(versions: list[str]) -> list[str]:
    ordered = sorted(versions, key=lambda v: tuple(int(p) for p in v.split(".")))
    return [ordered[0], ordered[-1]]


def test_the_linux_matrix_is_exactly_the_classifiers_and_windows_is_its_floor_and_ceiling():
    minors = _classifier_minors((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert minors, (
        "pyproject declares no Python classifiers; declare them, the matrix derives from them"
    )
    workflow = _yaml(_TESTS_WF)
    assert _matrix_versions(workflow, "test-matrix") == minors, (
        f"tests.yml's Linux matrix {_matrix_versions(workflow, 'test-matrix')} is not "
        f"pyproject's classifier list {minors}"
    )
    assert _matrix_versions(workflow, "test-windows") == _floor_and_ceiling(minors)


def test_the_matrix_check_catches_a_planted_classifier_the_matrix_does_not_run():
    """Planted: pyproject grows a 3.15 classifier and the workflow is not
    touched — the matrix must be reported stale, not silently narrower."""
    pyproject = (
        '[project]\nclassifiers = ["Programming Language :: Python :: 3",\n'
        ' "Programming Language :: Python :: 3.12", "Programming Language :: Python :: 3.15"]\n'
    )
    minors = _classifier_minors(pyproject)
    assert minors == ["3.12", "3.15"], "the bare `:: 3` classifier is not a minor"
    workflow = {
        "jobs": {"test-matrix": {"strategy": {"matrix": {"python-version": ["3.12"]}}}}
    }
    assert _matrix_versions(workflow, "test-matrix") != minors
    assert _floor_and_ceiling(["3.11", "3.9", "3.10"]) == ["3.9", "3.11"], (
        "numeric, not lexical"
    )


def _ruff_pin(workflow_text: str) -> str | None:
    """The exact ruff version the lint leg installs, or None when it floats."""
    m = _RUFF_PIN.search(workflow_text)
    return m.group(1) if m else None


def test_the_lint_leg_pins_ruff_to_an_exact_version():
    assert _ruff_pin(_TESTS_WF.read_text(encoding="utf-8")) is not None, (
        "the lint leg must `pip install ruff==X.Y.Z`; a floating linter changes its rules under you"
    )


def test_the_pin_check_catches_a_planted_floating_ruff():
    """Planted: the three spellings that look pinned and are not."""
    assert _ruff_pin("run: pip install ruff\n") is None
    assert _ruff_pin("run: pip install ruff>=0.16\n") is None
    assert _ruff_pin("run: pip install ruff~=0.16.7\n") is None
    assert _ruff_pin("run: pip install ruff==0.16.7\n") == "0.16.7"


def _gate_defects(workflow: dict) -> list[str]:
    """Everything wrong with the aggregate gate, as a list of reasons; empty
    when it needs every other job, always runs, and rejects every result but
    `success` for each of them, by name, in its own script."""
    jobs = workflow["jobs"]
    if _GATE_JOB not in jobs:
        return [f"no job named {_GATE_JOB!r}"]
    gate = jobs[_GATE_JOB]
    defects: list[str] = []
    legs = sorted(name for name in jobs if name != _GATE_JOB)
    needs = gate.get("needs") or []
    needs = [needs] if isinstance(needs, str) else list(needs)
    for missing in sorted(set(legs) - set(needs)):
        defects.append(f"gate does not need {missing!r}")
    if str(gate.get("if", "")).strip() not in ("always()", "${{ always() }}"):
        defects.append(
            "gate is not `if: always()`, so a failed leg skips it and the check never reports"
        )
    body = "\n".join(str(s.get("run", "")) for s in gate.get("steps", []))
    for leg in needs:
        if not re.search(
            rf"needs\.{re.escape(leg)}\.result\s*}}}}\"?\s*!=\s*['\"]success['\"]", body
        ):
            defects.append(
                f"gate does not reject every non-success result of {leg!r} "
                "(skipped and cancelled must fail it, not only failure)"
            )
    if "exit 1" not in body:
        defects.append("gate never exits non-zero")
    return defects


def test_the_aggregate_gate_needs_every_leg_and_rejects_anything_but_success():
    assert _gate_defects(_yaml(_TESTS_WF)) == []


def test_the_gate_check_catches_a_planted_gate_that_forgets_a_leg_or_passes_skipped():
    """Planted: a gate that needs only the Linux matrix, and one that checks
    for `failure` alone — the second passes a Windows leg that never ran."""
    forgetful = {
        "jobs": {
            "test-matrix": {},
            "test-windows": {},
            "lint": {},
            "test": {
                "needs": ["test-matrix"],
                "if": "always()",
                "steps": [
                    {
                        "run": 'if [ "${{ needs.test-matrix.result }}" != "success" ]; then exit 1; fi'
                    }
                ],
            },
        }
    }
    defects = _gate_defects(forgetful)
    assert (
        "gate does not need 'lint'" in defects
        and "gate does not need 'test-windows'" in defects
    )
    lenient = {
        "jobs": {
            "test-matrix": {},
            "test-windows": {},
            "test": {
                "needs": ["test-matrix", "test-windows"],
                "if": "always()",
                "steps": [
                    {
                        "run": 'if [ "${{ needs.test-matrix.result }}" != "success" ]; then exit 1; fi\n'
                        'if [ "${{ needs.test-windows.result }}" == "failure" ]; then exit 1; fi'
                    }
                ],
            },
        }
    }
    assert [d for d in _gate_defects(lenient) if "test-windows" in d], (
        "== failure lets skipped through"
    )
    no_always = {
        "jobs": {
            "a": {},
            "test": {
                "needs": ["a"],
                "steps": [
                    {
                        "run": 'if [ "${{ needs.a.result }}" != "success" ]; then exit 1; fi'
                    }
                ],
            },
        }
    }
    assert any("always()" in d for d in _gate_defects(no_always))
    assert _gate_defects({"jobs": {"a": {}}}) == ["no job named 'test'"]
