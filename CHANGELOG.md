# Changelog

## [1.7.1](https://github.com/willow-memory/ratatosk/compare/v1.7.0...v1.7.1) (2026-09-12)


### Fixed

* hooks, the Bash tool and the child environment work on Windows ([af16dec](https://github.com/willow-memory/ratatosk/commit/af16dec0361a7b0184b76f7483b1dad789bc25cb))

## [1.7.0](https://github.com/willow-memory/ratatosk/compare/v1.6.0...v1.7.0) (2026-09-11)


### Added

* activation daemon with Nestor seal watcher ([#42](https://github.com/willow-memory/ratatosk/issues/42)) ([4066517](https://github.com/willow-memory/ratatosk/commit/4066517dfdccbc2e4ee4ddbd5cc03634df1bb11e))
* **daemon:** add a generic append-only-JSONL seal watcher to SeatDaemon ([fa41449](https://github.com/willow-memory/ratatosk/commit/fa414490e5eb7764a4344f1a25745493541585a9))
* **daemon:** add the ratatosk-listen activation daemon ([1a85826](https://github.com/willow-memory/ratatosk/commit/1a8582674c1954d5fef54fc117dfd8489171099c))
* **listener:** BusListener dispatches WAKE to a seat activation callback, emits Grove heartbeats ([51ddf16](https://github.com/willow-memory/ratatosk/commit/51ddf16203f6f5a883cc4450f18fc9fd85dcad40))
* **protocol:** add a WAKE intent for fleet activation, gated open ([352686e](https://github.com/willow-memory/ratatosk/commit/352686e7da6e1ea5c0db147770e6a89bc69062f4))


### Fixed

* **daemon:** make SeatDaemon's seal predicate correct and overridable ([24d9989](https://github.com/willow-memory/ratatosk/commit/24d9989dd29a697f56b4574cd6b2ee5a871fab55))
* **daemon:** persist seal watcher offset per record, handle rotation, decouple poll cadence ([da485bb](https://github.com/willow-memory/ratatosk/commit/da485bb1863755f8570164c87e32079eb8288a42))

## [1.6.0](https://github.com/willow-memory/ratatosk/compare/v1.5.0...v1.6.0) (2026-09-10)


### Added

* **mcp:** reconnect when the server dies ([#37](https://github.com/willow-memory/ratatosk/issues/37)) ([6c9e6f4](https://github.com/willow-memory/ratatosk/commit/6c9e6f4d8a03dad1a0356cbe3c21e98bc85f9680))

## [1.5.0](https://github.com/willow-memory/ratatosk/compare/v1.4.4...v1.5.0) (2026-09-10)


### Added

* **crown:** give compaction a receipt ([#36](https://github.com/willow-memory/ratatosk/issues/36)) ([799794e](https://github.com/willow-memory/ratatosk/commit/799794e52e18f0abb6cb95fda465786ffe286551))

## [1.4.4](https://github.com/willow-memory/ratatosk/compare/v1.4.3...v1.4.4) (2026-09-10)


### Fixed

* **listener:** a one-row page is a page, and a refused reply is not a reply ([948451e](https://github.com/willow-memory/ratatosk/commit/948451ec9bf7a3ec84f2f6735f5bf1cc0269fd23))
* **listener:** a one-row page is a page, and a refused reply is not a reply ([#38](https://github.com/willow-memory/ratatosk/issues/38)) ([cedec4a](https://github.com/willow-memory/ratatosk/commit/cedec4a020acad170e6c7db3b5044d8a1106336d))

## [1.4.3](https://github.com/willow-memory/ratatosk/compare/v1.4.2...v1.4.3) (2026-09-10)


### Fixed

* **mcp:** decode concatenated results, and record real ones to prove it ([5f30e58](https://github.com/willow-memory/ratatosk/commit/5f30e58a5797a23a39d3cbf636bbf5ae78834768))
* **mcp:** decode concatenated results, and record real ones to prove it ([#34](https://github.com/willow-memory/ratatosk/issues/34)) ([fdfd491](https://github.com/willow-memory/ratatosk/commit/fdfd49135d657ff94770b6b0f7621d81fef22264))

## [1.4.2](https://github.com/willow-memory/ratatosk/compare/v1.4.1...v1.4.2) (2026-09-10)


### Fixed

* **crown:** do not claim to have written an empty session ([bcbd44f](https://github.com/willow-memory/ratatosk/commit/bcbd44fcf4aa3f71e24eb610005cdb7206c3ec0d))
* **crown:** do not claim to have written an empty session ([#32](https://github.com/willow-memory/ratatosk/issues/32)) ([006e562](https://github.com/willow-memory/ratatosk/commit/006e5625cb5196a6ea2d7334337b03a3786156aa))

## [1.4.1](https://github.com/willow-memory/ratatosk/compare/v1.4.0...v1.4.1) (2026-09-10)


### Fixed

* **grove:** give the sender one supported way to be bound ([279c791](https://github.com/willow-memory/ratatosk/commit/279c791e778e9e8561d1ba44966786ec4d80e9c4))
* **grove:** give the sender one supported way to be bound ([#30](https://github.com/willow-memory/ratatosk/issues/30)) ([fa4ec2d](https://github.com/willow-memory/ratatosk/commit/fa4ec2db778683b9efdee74035559548f6caeac7))

## [1.4.0](https://github.com/willow-memory/ratatosk/compare/v1.3.0...v1.4.0) (2026-09-10)


### Added

* **permission:** cite CONST-X-4, the clause this seam already enforces ([dc5cfe0](https://github.com/willow-memory/ratatosk/commit/dc5cfe0b22566d2fc292f98343e888f6d3537fa3))
* **permissions:** argument-scoped rules, and cite the clause the seam enforces ([#28](https://github.com/willow-memory/ratatosk/issues/28)) ([db3dbbd](https://github.com/willow-memory/ratatosk/commit/db3dbbd97287dfce79bf3c431d0d2643df135546))
* **policy:** scope a rule to the argument, not just the tool ([7461e31](https://github.com/willow-memory/ratatosk/commit/7461e31142437129f8819c98d9534627c048f068))

## [1.3.0](https://github.com/willow-memory/ratatosk/compare/v1.2.11...v1.3.0) (2026-09-10)


### Added

* **crown:** explain a verdict without running the tool ([3dfc0a9](https://github.com/willow-memory/ratatosk/commit/3dfc0a92621b8fda88dd7f990dcdb2506aad000f))
* **permissions:** mark unreachable rules, and explain a verdict without running it ([#26](https://github.com/willow-memory/ratatosk/issues/26)) ([819ef49](https://github.com/willow-memory/ratatosk/commit/819ef49e6225c4b1c7d3830e3f0cdb1f9199dab1))
* **policy:** report rules that can never fire ([aae1a13](https://github.com/willow-memory/ratatosk/commit/aae1a1350f806eb245d04a2e93457f34828917ca))

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
