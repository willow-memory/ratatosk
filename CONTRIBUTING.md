# Contributing

## Run the tests

```sh
pip install -e ".[dev]"
python -m pytest tests/ -q
```

That second line is the exact command CI runs on every supported Python
(`.github/workflows/tests.yml`), and the `test` check the branch ruleset
requires before a PR may merge. A PR says what it changed, how that was
verified — quote the command and its result — and what was seen and not
touched.

## Commit types decide releases

This repo merges with merge commits and releases with release-please, so
every conventional-commit type that is not hidden cuts a release on its
own, and the PR title counts as one more commit. The hidden set (`chore`,
`ci`, `docs`, `test`) and the reasoning behind it live beside the setting in
`release-please-config.json`; `.github/workflows/pr-title.yml` stops a title
from cutting a release its commits would not, and a release-cutting commit
from shipping nothing installable. The line is: does this change what
`pip install willow-ratatosk` gives someone?

## The fleet's conventions

`tests/test_fleet_conventions.py` holds this tree to the rules the fleet
publishes (`reconciler conventions --json`, willow-reconciler). The rules
are read from the vendored `tests/fleet_conventions.json`, never restated;
re-sync that file from the reconciler rather than editing it by hand.
