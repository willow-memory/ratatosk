# Changelog

## [1.2.11](https://github.com/willow-memory/ratatosk/compare/v1.2.10...v1.2.11) (2026-09-10)


### Fixed

* **crown:** enforce that --listen requires --mcp ([b49da70](https://github.com/willow-memory/ratatosk/commit/b49da70e462bb5ccda21907d27ab178038303292))
* **crown:** make the --listen path fail honestly ([#24](https://github.com/willow-memory/ratatosk/issues/24)) ([1a4abbb](https://github.com/willow-memory/ratatosk/commit/1a4abbbebe185a12431fbaabce0dbc94e5a6c2d9))
* **crown:** tear the MCP server down when the listener refuses ([c53e6c4](https://github.com/willow-memory/ratatosk/commit/c53e6c45ed891034b2d2fd1762a1eab7cb45aabf))

## [1.2.10](https://github.com/willow-memory/ratatosk/compare/v1.2.9...v1.2.10) (2026-09-07)


### Fixed

* **grove:** refuse an unset channel instead of defaulting to general ([70a994d](https://github.com/willow-memory/ratatosk/commit/70a994de5196ea960c396b48db79eb66f4e9e60f))
* **grove:** refuse an unset channel instead of defaulting to general ([#22](https://github.com/willow-memory/ratatosk/issues/22)) ([ae527e6](https://github.com/willow-memory/ratatosk/commit/ae527e60493ed89dc84e3fb7e5ec94e3438657af))

## [1.2.9](https://github.com/willow-memory/ratatosk/compare/v1.2.8...v1.2.9) (2026-09-07)


### Fixed

* **protocol:** close the last two defects, and stop BUGS.md lying ([a737cdf](https://github.com/willow-memory/ratatosk/commit/a737cdf404b474daf5af2e018c303da424940cd7))
* **protocol:** close the last two defects, and stop BUGS.md lying ([#20](https://github.com/willow-memory/ratatosk/issues/20)) ([be7b3cf](https://github.com/willow-memory/ratatosk/commit/be7b3cf012a34f3bc54fb7cd83c722ee7e8c1520))

## [1.2.8](https://github.com/willow-memory/ratatosk/compare/v1.2.7...v1.2.8) (2026-09-07)


### Fixed

* **test:** tomllib is 3.11+, and this package supports 3.10 ([5e7ad31](https://github.com/willow-memory/ratatosk/commit/5e7ad31c80ad8ac3b17b87735759c64dd4cc2f7b))

## [1.2.7](https://github.com/willow-memory/ratatosk/compare/v1.2.6...v1.2.7) (2026-09-07)


### Fixed

* **crown:** compaction never orphans a tool_result ([1932c69](https://github.com/willow-memory/ratatosk/commit/1932c69bdb81e8dd81135778b293dbd8f65ccaf1))
* **crown:** compaction never orphans a tool_result ([#17](https://github.com/willow-memory/ratatosk/issues/17)) ([24f1378](https://github.com/willow-memory/ratatosk/commit/24f13786d43a000231dae7529c562c900664e5dc))
* **tools:** one error shape, an honest schema, signalled truncation ([ecec4a9](https://github.com/willow-memory/ratatosk/commit/ecec4a931c44bfd09a67634919f627f49ab1821d))
* **tools:** one error shape, an honest schema, signalled truncation ([#15](https://github.com/willow-memory/ratatosk/issues/15)) ([fb0698b](https://github.com/willow-memory/ratatosk/commit/fb0698b7b3221390c95d15cb983efe0615e54bcf))

## [1.2.6](https://github.com/willow-memory/ratatosk/compare/v1.2.5...v1.2.6) (2026-09-07)


### Fixed

* **hooks:** a hook runs as what it is, and PreTool can block ([28c5315](https://github.com/willow-memory/ratatosk/commit/28c5315b3ef6c79b3049c4366828925562d9c60b))
* **hooks:** a hook runs as what it is, and PreTool can block ([#13](https://github.com/willow-memory/ratatosk/issues/13)) ([1133bb3](https://github.com/willow-memory/ratatosk/commit/1133bb33b803c2290a00b1df9e197b19e0db3ecf))

## [1.2.5](https://github.com/willow-memory/ratatosk/compare/v1.2.4...v1.2.5) (2026-09-07)


### Fixed

* **tools:** every verdict goes through one chokepoint ([8d793d9](https://github.com/willow-memory/ratatosk/commit/8d793d9ec14b0ee98329e2086570f03d262a238e))
* **tools:** every verdict goes through one chokepoint ([#11](https://github.com/willow-memory/ratatosk/issues/11)) ([9bdc833](https://github.com/willow-memory/ratatosk/commit/9bdc8337e97700056ac91db360ce73df2c98e14f))

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
