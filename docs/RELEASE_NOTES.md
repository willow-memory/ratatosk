# Release Notes (Draft)

## Ratatosk Runtime Blueprint Update

This release delivers a major Ratatosk runtime expansion focused on session UX, safety controls, and operational reliability.

### Highlights

- Introduced a modular command router in `ratatosk/crown.py`.
- Added new CLI commands:
  - `/sessions`
  - `/resume`
  - `/doctor`
  - `/export`
  - `/permissions`
  - `/hooks`
  - `/compact`
  - `/help`
- Added sqlite-backed session indexing and discovery in `ratatosk/history.py`.
- Added persistent policy rule management in `ratatosk/policy.py` with `allow`, `deny`, and `confirm`.
- Added event-driven hook runtime in `ratatosk/hooks.py` for lifecycle and tool events.
- Integrated policy and hook execution into tool dispatch in `ratatosk/tools.py`.
- Expanded tests across command flow, history, hooks, and policy behavior.

### Verification

- Test suite result: `19 passed` via `pytest -q`.

## Unreleased — behaviour change: `--trust` no longer overrides explicit rules

`--trust` previously bypassed the policy entirely, which made an explicit
`Write -> confirm` rule a no-op — the opposite of what writing the rule was for.
It is now a layer *below* explicit rules: it relaxes the unmatched default
(`PolicyStore.decide` returns `confirm` for anything no rule matches) and
nothing else.

If you ran `--trust` and relied on it silencing a rule you had written, remove
the rule with `/permissions` rather than relying on the flag.

Also: `ratatosk.tools.dispatch` now raises `ratatosk.permission.NeedsConfirmation`
on a `confirm` verdict instead of falling through and executing. `deny` still
returns a JSON error value. Callers embedding `dispatch` directly must handle
the exception; not handling it fails closed.
