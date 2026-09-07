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
