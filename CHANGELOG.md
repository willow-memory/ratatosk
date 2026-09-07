# Changelog

## [1.2.1](https://github.com/willow-memory/ratatosk/compare/v1.2.0...v1.2.1) (2026-09-07)


### Fixed

* **mcp:** forward the fleet environment to the spawned willow-mcp ([c880b51](https://github.com/willow-memory/ratatosk/commit/c880b5178dc6a6619dd421a8fd0dd5c280f7df42))
* **mcp:** repair grove/listener wiring against mcp SDK 2.0 ([4f8e4b9](https://github.com/willow-memory/ratatosk/commit/4f8e4b9a00a4d064c8b9cefac059e8fe3f2ff3b8))
* **mcp:** repair the MCP path — SDK 2.0 drift, false Grove receipts, stripped server env ([#1](https://github.com/willow-memory/ratatosk/issues/1)) ([8736689](https://github.com/willow-memory/ratatosk/commit/873668927ef8db95d435f9920da951c1b5053c78))

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
