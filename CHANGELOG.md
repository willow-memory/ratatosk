# Changelog

## [1.2.0](https://github.com/willow-memory/ratatosk/compare/v1.1.0...v1.2.0) (2026-09-07)

### Added

- Command router in `ratatosk/crown.py` with `/sessions`, `/resume`, `/doctor`, `/export`, `/permissions`, `/hooks`, `/compact`, and `/help`.
- Sqlite-backed session index (`ratatosk/history.py`) for discovery and resume.
- Persistent tool policy rules (`ratatosk/policy.py`) with allow/deny/confirm behavior.
- Lifecycle and tool hook runtime (`ratatosk/hooks.py`).
- PyPI distribution as `willow-ratatosk` (import remains `ratatosk`).

## [1.1.0](https://github.com/willow-memory/ratatosk/releases/tag/v1.1.0) (2026-09-06)

### Added

- Initial platform session runtime: MCP client, capability gate, Grove bus, JSONL sessions, tier-0 deposit sync.
- Playground promotion from safe-app-store to `willow-memory/ratatosk`.
