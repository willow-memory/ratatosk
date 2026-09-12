"""This tree, held to the fleet's published conventions.

Fleet plan decision 4: every repo carries this file, and the rules it checks
are READ from the fleet's published document — `reconciler conventions
--json` (willow-reconciler >= 0.6.0) — never restated here. The document is
vendored beside this file as `tests/fleet_conventions.json` so the check runs
offline and on every Python in the matrix, and it is pinned by hash so a
hand edit is a test failure rather than a quiet fork of the fleet's rules.
When `reconciler` happens to be importable, the vendored copy is also held
equal to the live one.

The consumer half is the reconciler's own consumer test, adapted to this
tree: the pile is `docs/ideas.md` (numbered, in the reconciler's form since
E3-piles), the test command is the one CI runs and CONTRIBUTING.md names.
Five checks run against the real tree; four plants show each check can fire.

`required_when_pile_exists` names `.github/workflows/trailers.yml`. It was
xfail(strict) here between Wave 2 and Wave 3, while the pile was unnumbered
and the workflow absent; E3-trailers added the workflow and the mark came
off, as a strict xfail is meant to.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = Path(__file__).resolve().parent / "fleet_conventions.json"

#: sha256 of the vendored document's bytes, exactly as `reconciler conventions
#: --json` (willow-reconciler 0.6.0) wrote them: `json.dumps(..., indent=2)`
#: plus a trailing newline.
PINNED_SHA256 = "8c2ba122a7100141200d8c76ad086339f984446ab7e90dd9c27a092dbf7f5335"
PINNED_FROM = "willow-reconciler 0.6.0"
SCHEMA = "willow-fleet-conventions/1"

RULES = json.loads(DOCUMENT.read_text(encoding="utf-8"))

RELEASE_PLEASE = ".github/workflows/release-please.yml"
RELEASE_CONFIG = "release-please-config.json"
CONTRIBUTING = "CONTRIBUTING.md"
PILE = "docs/ideas.md"
ARMS_AUTOMERGE = "gh pr merge --auto"
#: Exactly what `.github/workflows/tests.yml` runs and CONTRIBUTING.md quotes.
TEST_COMMAND = "python -m pytest tests/ -q"


# ── the document itself ──────────────────────────────────────────────────────


def _document_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_the_vendored_document_is_the_one_the_fleet_published():
    assert RULES["schema"] == SCHEMA
    assert _document_hash(DOCUMENT.read_bytes()) == PINNED_SHA256, (
        f"tests/fleet_conventions.json no longer matches {PINNED_FROM}: re-sync "
        "from `reconciler conventions --json` (willow-reconciler >=0.6.0), or "
        "record the decision"
    )


def test_the_pin_catches_a_planted_one_byte_change():
    """Planted: the same document with one byte changed. Still valid JSON,
    still the same rules to a reader who does not check — and a different
    hash, which is the point of pinning bytes rather than meaning."""
    original = DOCUMENT.read_bytes()
    altered = original.replace(b'"chore"', b'"chorf"', 1)
    assert altered != original, (
        "the plant did not land; the document has no chore entry"
    )
    json.loads(altered)  # still parses — a semantic check would not notice
    assert _document_hash(altered) != PINNED_SHA256
    assert _document_hash(original) == PINNED_SHA256


def test_the_vendored_document_equals_the_live_one_when_the_reconciler_is_installed():
    conventions = pytest.importorskip(
        "reconciler.conventions",
        reason="willow-reconciler not installed; the vendored copy is pinned by hash instead",
    )
    assert conventions.conventions() == RULES, (
        "the installed reconciler publishes different conventions than the "
        "vendored copy: re-sync tests/fleet_conventions.json and re-pin"
    )


# ── the consumer: this tree against the published rules ─────────────────────


def _arms_automerge(root: Path) -> bool:
    workflow = root / RELEASE_PLEASE
    return workflow.exists() and ARMS_AUTOMERGE in workflow.read_text(encoding="utf-8")


def _missing_when_armed(root: Path, required: list[str]) -> list[str]:
    if not _arms_automerge(root):
        return []
    return [f for f in required if not (root / f).exists()]


def _config_hidden_types(config_text: str) -> set[str]:
    sections = json.loads(config_text)["packages"]["."]["changelog-sections"]
    return {s["type"] for s in sections if s.get("hidden")}


def _config_missing_comments(config_text: str, required: list[str]) -> list[str]:
    package = json.loads(config_text)["packages"]["."]
    return [c for c in required if c not in package]


def _missing_when_pile_exists(root: Path, required: list[str]) -> list[str]:
    if not (root / PILE).exists():
        return []
    return [f for f in required if not (root / f).exists()]


def _names_test_command(contributing_text: str) -> bool:
    return TEST_COMMAND in contributing_text


def test_pr_title_guard_is_present_wherever_automerge_is_armed():
    assert _arms_automerge(REPO_ROOT)
    assert (
        _missing_when_armed(
            REPO_ROOT, RULES["required_when_release_please_arms_automerge"]
        )
        == []
    )


def test_the_configs_hidden_set_equals_the_published_set():
    text = (REPO_ROOT / RELEASE_CONFIG).read_text(encoding="utf-8")
    assert _config_hidden_types(text) == set(RULES["hidden_types"])


def test_the_config_carries_every_required_reasoning_comment():
    text = (REPO_ROOT / RELEASE_CONFIG).read_text(encoding="utf-8")
    assert _config_missing_comments(text, RULES["required_config_comments"]) == []


def test_contributing_names_the_test_command():
    assert RULES["contributing_must_name_test_command"] is True
    assert _names_test_command((REPO_ROOT / CONTRIBUTING).read_text(encoding="utf-8"))


def test_trailers_workflow_is_present_because_a_pile_exists():
    assert (REPO_ROOT / PILE).exists()
    assert (
        _missing_when_pile_exists(REPO_ROOT, RULES["required_when_pile_exists"]) == []
    )


# ── the plants ───────────────────────────────────────────────────────────────


def _tree(
    tmp_path: Path, label: str, *, arms: bool, files: tuple[str, ...] = ()
) -> Path:
    root = tmp_path / label
    (root / ".github" / "workflows").mkdir(parents=True)
    body = "jobs:\n  release-please:\n    steps:\n      - run: |\n"
    body += f'          {ARMS_AUTOMERGE} "$pr"\n' if arms else "          gh pr list\n"
    (root / RELEASE_PLEASE).write_text(body, encoding="utf-8")
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("# planted\n", encoding="utf-8")
    return root


def test_the_armed_tree_check_fires_on_a_planted_tree_missing_the_guard(tmp_path):
    required = RULES["required_when_release_please_arms_automerge"]
    assert _missing_when_armed(_tree(tmp_path, "bare", arms=True), required) == required
    assert (
        _missing_when_armed(
            _tree(tmp_path, "guarded", arms=True, files=tuple(required)), required
        )
        == []
    )
    assert _missing_when_armed(_tree(tmp_path, "manual", arms=False), required) == []


def test_the_hidden_set_check_catches_a_planted_config_that_unhides_ci():
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
    assert _config_hidden_types(planted) == {"chore", "docs", "test"}
    assert _config_missing_comments(planted, RULES["required_config_comments"]) == [
        "$comment-hidden-rule"
    ]


def test_the_pile_check_fires_on_a_planted_tree_with_a_pile_and_no_verify_gate(
    tmp_path,
):
    required = RULES["required_when_pile_exists"]
    with_pile = _tree(tmp_path, "pile", arms=False, files=(PILE,))
    assert _missing_when_pile_exists(with_pile, required) == required
    gated = _tree(tmp_path, "gated", arms=False, files=(PILE, *required))
    assert _missing_when_pile_exists(gated, required) == []


def test_the_contributing_check_catches_a_planted_contributing_without_the_command():
    assert not _names_test_command("# Contributing\n\nRun the tests before pushing.\n")
    assert _names_test_command(f"```sh\n{TEST_COMMAND}\n```\n")
