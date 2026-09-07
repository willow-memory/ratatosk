# Changelog

## Unreleased

- Added command router architecture in `ratatosk/crown.py`.
- Added `/sessions`, `/resume`, `/doctor`, `/export`, `/permissions`, `/hooks`, `/compact`, and `/help`.
- Added sqlite-backed history index (`ratatosk/history.py`) for session discovery and resume.
- Added persistent tool policy rules (`ratatosk/policy.py`) with allow/deny/confirm behavior.
- Added lifecycle/tool hook runtime (`ratatosk/hooks.py`) with event-based execution.
- Added new tests for history, policy, hooks, tools integration, and command flows.

