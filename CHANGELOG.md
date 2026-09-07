# Changelog

## [1.2.4](https://github.com/willow-memory/ratatosk/compare/v1.2.3...v1.2.4) (2026-09-07)


### Fixed

* **crown:** a file-loaded key never enters the environment ([fc39732](https://github.com/willow-memory/ratatosk/commit/fc39732fa2b6f6d928f9dd51bb8a29c02924a021))
* **crown:** a file-loaded key never enters the environment ([#9](https://github.com/willow-memory/ratatosk/issues/9)) ([c0a12f7](https://github.com/willow-memory/ratatosk/commit/c0a12f76347fa71d0a08324598c26237ce8e00eb))

## [1.2.3](https://github.com/willow-memory/ratatosk/compare/v1.2.2...v1.2.3) (2026-09-07)


### Fixed

* **crown:** an interrupt ends the turn, not the session ([a897374](https://github.com/willow-memory/ratatosk/commit/a8973744daa99dfe4f18e2dc1d8b3e0f6bc9044b))
* **crown:** an interrupt ends the turn, not the session ([#7](https://github.com/willow-memory/ratatosk/issues/7)) ([29a7998](https://github.com/willow-memory/ratatosk/commit/29a799837657ddb61fc1368f13d2f309492b3850))

## [1.2.2](https://github.com/willow-memory/ratatosk/compare/v1.2.1...v1.2.2) (2026-09-07)


### Fixed

* **listener:** a seat never answers its own post ([dc5b6be](https://github.com/willow-memory/ratatosk/commit/dc5b6bead1d6cbf8534e5391c5cef2152a8800fb))
* **listener:** a seat never answers its own post ([#5](https://github.com/willow-memory/ratatosk/issues/5)) ([8e2fbd6](https://github.com/willow-memory/ratatosk/commit/8e2fbd66ec7cac93549419f6e4b02e055dae281c))

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
