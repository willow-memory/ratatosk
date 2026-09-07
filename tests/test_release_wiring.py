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
no release-body sync, no pr-title workflow, and no second file storing a version.
The two checks that are *new* here — the arming step reporting its failure, and
the required check naming a job that exists — are this repo's own scar.
"""
from __future__ import annotations

import fnmatch
import json
import re
import tomllib  # stdlib from 3.11
from pathlib import Path

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
    assert any(req.lower().startswith(("pyyaml", "yaml")) for req in dev), \
        f"PyYAML must be in the dev extra or this whole file skips unnoticed: {dev}"


def test_the_tag_release_please_creates_matches_what_release_yml_listens_for():
    """With `include-component-in-tag` unset it defaults to *true* and the tag
    becomes `willow-ratatosk-vX.Y.Z`, which `v*` does not match — so the tag is
    created and nothing publishes, with no error anywhere. Observed on
    willow-mcp#256."""
    cfg = _package_config()
    version = _json(_MANIFEST)["."]
    tag = (f"{cfg['package-name']}-v{version}"
           if cfg.get("include-component-in-tag", True) else f"v{version}")

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
    assert "version" not in pyproject["project"], \
        "a literal project.version is a second source of truth"
    assert pyproject["tool"]["hatch"]["version"]["source"] == "vcs"
    assert not _package_config().get("extra-files"), \
        "nothing in this repo stores a version, so nothing needs bumping"


# A credential whose events actually trigger workflows. What is NOT acceptable is
# GITHUB_TOKEN, whose events GitHub suppresses — the release PR merges, no tag
# workflow fires, nothing publishes. jeles lost three releases to it.
NON_SUPPRESSED_CREDENTIALS = (
    "RELEASE_PLEASE_TOKEN",              # fine-grained PAT (being retired)
    "steps.app-token.outputs.token",     # willow-ci App installation token
)


def _names_a_non_suppressed_credential(value: object) -> bool:
    return any(c in str(value) for c in NON_SUPPRESSED_CREDENTIALS)


def test_release_automation_uses_a_non_suppressed_credential_everywhere():
    used: set[str] = set()
    values: list[str] = []
    for step in _rp_steps():
        for value in list((step.get("env") or {}).values()) + \
                     list((step.get("with") or {}).values()):
            values.append(str(value))
            used.update(re.findall(r"secrets\.([A-Z_]+)", str(value)))
    assert any(_names_a_non_suppressed_credential(v) for v in values), \
        f"no non-suppressed credential anywhere in the job; secrets seen: {used}"
    assert "GITHUB_TOKEN" not in used, \
        f"GITHUB_TOKEN's events do not trigger workflows; found {used}"


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
                assert "--squash" not in line, \
                    "this fleet merges with merge commits; squash breaks release-please's parse"


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
        code = "\n".join(line for line in run.splitlines()
                         if not line.strip().startswith("#"))
        assert "|| true" not in code, \
            "`|| true` is what hid the unarmed auto-merge for six releases"
        assert "::warning::" in code or "::error::" in code, \
            "a failure to arm must be annotated, not swallowed"


def test_the_checkout_can_see_history_and_tags():
    checkout = next(s for s in _rp_steps()
                    if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0, "needs full history for the range"
    assert checkout["with"]["fetch-tags"] is True, "needs tags to find the previous release"


def test_the_required_check_names_a_job_that_actually_exists():
    """The branch ruleset requires the `test` check. If that job is renamed, the
    ruleset silently requires a check nothing produces and every PR blocks
    forever — the mirror image of requiring nothing, which is where this repo
    started."""
    jobs = _yaml(_TESTS_WF)["jobs"]
    assert REQUIRED_CHECK in jobs, \
        f"the ruleset requires the check {REQUIRED_CHECK!r}, absent from {sorted(jobs)}"


def test_the_required_check_actually_gates_the_matrix():
    """`test` must fail when a matrix leg fails. A green aggregate job that does
    not depend on the matrix would satisfy the ruleset while proving nothing."""
    job = _yaml(_TESTS_WF)["jobs"][REQUIRED_CHECK]
    needs = job.get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    assert "test-matrix" in needs, \
        f"{REQUIRED_CHECK!r} must depend on test-matrix; needs={needs}"
    body = " ".join(str(s.get("run", "")) + str(s.get("if", "")) for s in job["steps"])
    assert "test-matrix" in body and "exit 1" in body, \
        "the aggregate job must assert the matrix succeeded, not merely follow it"


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
        assert cfg.get(flag) is False, \
            f"{flag} should stay false: inert now, correct if this ever forks a 0.x line"
