## [1.14.1](https://github.com/Jota-project/jota-gateway/compare/v1.14.0...v1.14.1) (2026-07-15)


### Bug Fixes

* close socket on handshake failure in all service clients (issue [#96](https://github.com/Jota-project/jota-gateway/issues/96)) ([#97](https://github.com/Jota-project/jota-gateway/issues/97)) ([212966e](https://github.com/Jota-project/jota-gateway/commit/212966e51aef3385de248d93dd8b0309cf64f3c9))

# [1.14.0](https://github.com/Jota-project/jota-gateway/compare/v1.13.0...v1.14.0) (2026-07-12)


### Features

* require auth for /v1/* from untrusted origins (issue [#52](https://github.com/Jota-project/jota-gateway/issues/52)) ([#89](https://github.com/Jota-project/jota-gateway/issues/89)) ([fca1932](https://github.com/Jota-project/jota-gateway/commit/fca1932e947b85025961e1ae424ae112d82948b9))

# [1.13.0](https://github.com/Jota-project/jota-gateway/compare/v1.12.1...v1.13.0) (2026-07-11)


### Features

* automatic reconnection for Transcriber and TTS, proactive orchestrator broadcast ([#88](https://github.com/Jota-project/jota-gateway/issues/88)) ([80a7cd1](https://github.com/Jota-project/jota-gateway/commit/80a7cd1229f91d540e210056c449ebb9341707d5))

## [1.12.1](https://github.com/Jota-project/jota-gateway/compare/v1.12.0...v1.12.1) (2026-07-10)


### Bug Fixes

* collapse multi-step push replies into a single turn_start/turn_end pair (issue [#84](https://github.com/Jota-project/jota-gateway/issues/84)) ([#87](https://github.com/Jota-project/jota-gateway/issues/87)) ([3ada105](https://github.com/Jota-project/jota-gateway/commit/3ada1050aa5ebd7b0b7bf91f3e8c0663432e50b8))

# [1.12.0](https://github.com/Jota-project/jota-gateway/compare/v1.11.1...v1.12.0) (2026-07-03)


### Features

* Alembic-backed schema migrations for SQLite ([#75](https://github.com/Jota-project/jota-gateway/issues/75)) ([d6b3bdc](https://github.com/Jota-project/jota-gateway/commit/d6b3bdc49e5202fdabcf1a191f277acf8e47390d)), closes [#73](https://github.com/Jota-project/jota-gateway/issues/73)

## [1.11.1](https://github.com/Jota-project/jota-gateway/compare/v1.11.0...v1.11.1) (2026-07-03)


### Bug Fixes

* fetch agent roster via agents.list, not hello-ok snapshot ([e675493](https://github.com/Jota-project/jota-gateway/commit/e675493ef01b1b1bfc8ddd1d8016b9819e64ded2))
* fetch agent roster via agents.list, not hello-ok snapshot ([#71](https://github.com/Jota-project/jota-gateway/issues/71)) ([54c91a8](https://github.com/Jota-project/jota-gateway/commit/54c91a8c24af3d3fa69673c52f0f701efc1d9ed8))

# [1.11.0](https://github.com/Jota-project/jota-gateway/compare/v1.10.0...v1.11.0) (2026-07-03)


### Bug Fixes

* detect turn completion via chat.state=='final' instead of a second res frame ([4458996](https://github.com/Jota-project/jota-gateway/commit/4458996a33915f0ff2ba7bbc0d2ab462cc7394d8))
* turn-completion detection + surface OpenClaw tool calls (opt-in) ([#70](https://github.com/Jota-project/jota-gateway/issues/70)) ([87f4571](https://github.com/Jota-project/jota-gateway/commit/87f4571647f37bcbc9f5b367a7dd5a4d19b964eb))


### Features

* add tool_calls_enabled per-client config flag (default off) ([de940d5](https://github.com/Jota-project/jota-gateway/commit/de940d570c7652e275ce833364921bc3cf84a069))
* add ToolCallEvent data model and extend OrchestratorEvent ([89e0be7](https://github.com/Jota-project/jota-gateway/commit/89e0be7c0bdd885f8c8269a6dd1e99a0d874b765))
* forward tool_call events to an on_tool_call callback ([fc5db94](https://github.com/Jota-project/jota-gateway/commit/fc5db94fef626a5e37a8cb39a11e5ff8a2860633))
* route session.tool events to turn queue / push bridge ([24cc874](https://github.com/Jota-project/jota-gateway/commit/24cc8746053ebe89c3ca1e8d98f4bf8004cfefd1))
* send tool_call WS messages for enabled clients (normal + push turns) ([56f1db3](https://github.com/Jota-project/jota-gateway/commit/56f1db3899d5d55ad56d6c1c35012afdb0cb5b1b))
* yield tool_call OrchestratorEvents from stream_response ([af7cf84](https://github.com/Jota-project/jota-gateway/commit/af7cf8491a8ece33de1b69e6b556f22545ad6539))

# [1.10.0](https://github.com/Jota-project/jota-gateway/compare/v1.9.0...v1.10.0) (2026-07-01)


### Bug Fixes

* log orchestrator errors in pipe_tokens() — was silently swallowed before ([02d6403](https://github.com/Jota-project/jota-gateway/commit/02d6403ea107c905f6a4053d611261bae7621968))
* revert chat.abort behavior change, fix test to race genuine in-flight cancellation ([970d19c](https://github.com/Jota-project/jota-gateway/commit/970d19c84f97e1534b4c1bfac27410fa4a5ada8a))


### Features

* add frames.py — isolated OpenClaw wire-format constructors with regression tests ([711701b](https://github.com/Jota-project/jota-gateway/commit/711701b8bc73dbd3b3cac0e682c5c7effb32bfd7))

# [1.9.0](https://github.com/Jota-project/jota-gateway/compare/v1.8.0...v1.9.0) (2026-07-01)


### Bug Fixes

* replace httpx exceptions with ClientNotFound/ClientInactive in WS handshake ([defd90a](https://github.com/Jota-project/jota-gateway/commit/defd90a82ee86178f87c425615988f05979bb279))


### Features

* add client_key param to CLI/API, seed hab_sito + openclaw clients, infra persistence ([fe8a106](https://github.com/Jota-project/jota-gateway/commit/fe8a10662af36e1d5a495ba51f224f0e360431f3))
* add local exceptions, Client.name field, and admin Pydantic schemas ([a374066](https://github.com/Jota-project/jota-gateway/commit/a3740668b8dc2d25866cf441a3cba829a33c55d8))
* add SQLModel ClientRecord + database engine ([dbcf7ec](https://github.com/Jota-project/jota-gateway/commit/dbcf7ec08c3254e6a4475d98eb190a0b25827b06))
* implement admin client CRUD, init DB in lifespan, fix test fixtures with StaticPool ([099010c](https://github.com/Jota-project/jota-gateway/commit/099010c0b4bac023000ef20217b97c66f92e60ca))
* push_enabled, max_silence_turns, schema cleanup + full CLAUDE.md update ([d02dd57](https://github.com/Jota-project/jota-gateway/commit/d02dd57b1f236e03064067423ca1ec3dcf908292))
* remove JOTA_DB settings, add DATABASE_URL, clean deps.py ([4709b68](https://github.com/Jota-project/jota-gateway/commit/4709b6881b68c6eb997b94107e03ac48acf4a11a))
* replace HTTP db_client with local SQLite client, keep same interface ([80674f0](https://github.com/Jota-project/jota-gateway/commit/80674f0b37ad2a18e19616009162220c3d769766))
* replace jota-db with local SQLite — client identity, config & admin API ([#67](https://github.com/Jota-project/jota-gateway/issues/67)) ([26e48a8](https://github.com/Jota-project/jota-gateway/commit/26e48a875b1c5103ce65c1b3666828e77f5c438e))

# [1.8.0](https://github.com/Jota-project/jota-gateway/compare/v1.7.0...v1.8.0) (2026-06-30)


### Bug Fixes

* remove unused imports flagged by ruff (status, AsyncMock) ([0899654](https://github.com/Jota-project/jota-gateway/commit/0899654f763d32ab22cb0c0b19b101accfbb19be))


### Features

* /admin/* router — sessions, orchestrators observability + client CRUD stubs ([fd772c5](https://github.com/Jota-project/jota-gateway/commit/fd772c5fa29878f96f1a0f91fb7c920a63bd77d4))
* add ADMIN_TOKEN, get_admin_auth dependency, get_name() on orchestrator, fix /v1/models id ([7268450](https://github.com/Jota-project/jota-gateway/commit/72684503cecf366f7aa6289dbdf6d4fc60590f56))
* API redesign — typed WS protocol, admin routes, health endpoints ([#66](https://github.com/Jota-project/jota-gateway/issues/66)) ([c013ee9](https://github.com/Jota-project/jota-gateway/commit/c013ee9ddd559abf1ca3a67cedd20d3e09af3deb))
* binary audio frames include [0xA1][turn_seq uint16 BE] header for turn identification ([54a55d3](https://github.com/Jota-project/jota-gateway/commit/54a55d37b41b4251d5bde84a0021da1637f16816))
* replace /api/health with /healthz (liveness) and /ready (readiness, 503 on orchestrator down) ([2cf3a19](https://github.com/Jota-project/jota-gateway/commit/2cf3a1960bccec855cdacdc67e7dff41a9b87a5e))
* send ready message after WS handshake health check ([44e5b2b](https://github.com/Jota-project/jota-gateway/commit/44e5b2b181aabb8f4a6451c59e46c5321acd5a9a))
* typed WS protocol — turn_start/end, token/text, error/code/fatal, status/state ([f71deb8](https://github.com/Jota-project/jota-gateway/commit/f71deb8bc8f648a231f289ef7b3e4bb6c19c4337))

# [1.7.0](https://github.com/Jota-project/jota-gateway/compare/v1.6.4...v1.7.0) (2026-06-30)


### Features

* OpenClaw multiplexed concurrent sessions + agent-initiated push delivery ([#64](https://github.com/Jota-project/jota-gateway/issues/64)) ([112a388](https://github.com/Jota-project/jota-gateway/commit/112a38813e5827737c41516732c8a3e28d2336f4))

## [1.6.4](https://github.com/Jota-project/jota-gateway/compare/v1.6.3...v1.6.4) (2026-06-26)


### Bug Fixes

* use gateway-client id in OpenClaw connect handshake ([#57](https://github.com/Jota-project/jota-gateway/issues/57)) ([70e4f14](https://github.com/Jota-project/jota-gateway/commit/70e4f144bb4cfc2ae52eca05216300940fbc10f5)), closes [#53](https://github.com/Jota-project/jota-gateway/issues/53)

## [1.6.3](https://github.com/Jota-project/jota-gateway/compare/v1.6.2...v1.6.3) (2026-06-26)


### Bug Fixes

* don't crash on startup if orchestrator is unreachable ([#56](https://github.com/Jota-project/jota-gateway/issues/56)) ([4053761](https://github.com/Jota-project/jota-gateway/commit/4053761ef3568244c25ad27e9f3efc050a8f2a38))

## [1.6.2](https://github.com/Jota-project/jota-gateway/compare/v1.6.1...v1.6.2) (2026-06-26)


### Bug Fixes

* remove spurious session_key from OpenClawClient constructor in registry ([#55](https://github.com/Jota-project/jota-gateway/issues/55)) ([fdaf662](https://github.com/Jota-project/jota-gateway/commit/fdaf662bc753017a17a9cff9f1d86a31104bf984)), closes [#53](https://github.com/Jota-project/jota-gateway/issues/53)

## [1.6.1](https://github.com/Jota-project/jota-gateway/compare/v1.6.0...v1.6.1) (2026-06-26)


### Bug Fixes

* add session_key to OrchestratorProtocol and propagate through stack ([#54](https://github.com/Jota-project/jota-gateway/issues/54)) ([9e2d6fe](https://github.com/Jota-project/jota-gateway/commit/9e2d6fedf7f0575dd599793c790ac34d0f115e63)), closes [#40](https://github.com/Jota-project/jota-gateway/issues/40) [#41](https://github.com/Jota-project/jota-gateway/issues/41) [#42](https://github.com/Jota-project/jota-gateway/issues/42) [#53](https://github.com/Jota-project/jota-gateway/issues/53) [#44](https://github.com/Jota-project/jota-gateway/issues/44) [#45](https://github.com/Jota-project/jota-gateway/issues/45)

# [1.6.0](https://github.com/Jota-project/jota-gateway/compare/v1.5.2...v1.6.0) (2026-06-26)


### Bug Fixes

* add agent field to Handshake schema, remove unused ClientConfig fields ([9cd454b](https://github.com/Jota-project/jota-gateway/commit/9cd454be5f0f5b7042299dd05a4f54a2355e4587))
* **monitoring:** scope latency helpers by turn, freeze PipelineEvent ([0869b30](https://github.com/Jota-project/jota-gateway/commit/0869b301eae0e9082f80e9112fd0d8ec550bfc8a))
* **monitoring:** scope session avg latencies by turn, add averaging tests ([8a5c7e2](https://github.com/Jota-project/jota-gateway/commit/8a5c7e24311e9b436ab48ff9a47e53ba617f77cf))
* **monitoring:** tighten SessionRegistry types and re-register ordering ([ba475dc](https://github.com/Jota-project/jota-gateway/commit/ba475dcd8924265f5a53ab512d22b3f35b1ce7bb))
* **orchestrators:** use gateway-client id, backend mode, and sessionKey format for OpenClaw protocol v4 ([9f8b55d](https://github.com/Jota-project/jota-gateway/commit/9f8b55dedf28eaf96688acc6b3cce83cf2a002ec))
* pin pytest and pytest-asyncio versions to avoid CI hang ([823657c](https://github.com/Jota-project/jota-gateway/commit/823657cf8012de728dcb51cd96e5fe849393b30d))


### Features

* add _NullWS for HTTP pipeline tracking ([93ba5dd](https://github.com/Jota-project/jota-gateway/commit/93ba5dd1a786d3402f672608423aa81da3336ae3))
* add call_orchestrator shared helper ([b1f95f8](https://github.com/Jota-project/jota-gateway/commit/b1f95f8ee021a01b4e859469673c172bfb5c6fe6))
* add make_session_key pure function ([e540099](https://github.com/Jota-project/jota-gateway/commit/e5400998aec1bb2901c6f0cb0ff4bc83719538c9))
* **api:** add GET /orchestrators/{name}/status and POST .../reconnect endpoints ([37620cb](https://github.com/Jota-project/jota-gateway/commit/37620cbe7a696aab14309e779ecac7ed59eba21a))
* **api:** add OpenAI-compatible /v1/chat/completions and /v1/models endpoints ([6a2be16](https://github.com/Jota-project/jota-gateway/commit/6a2be16cc7fb967ba4c2fb5bc4f547fd03b6fb34))
* bypass OpenClaw para /v1/chat/completions con LLM directo ([20582e6](https://github.com/Jota-project/jota-gateway/commit/20582e65eb41b1bdd65913d08f1d815538c27dea))
* **config:** add DEFAULT_ORCHESTRATOR, OPENCLAW_PORT, OPENCLAW_TOKEN ([bcf992f](https://github.com/Jota-project/jota-gateway/commit/bcf992fe3a03ad97bd9e5b610d1d6118dcd1aa88))
* **config:** add orchestrator reconnect settings ([273d42c](https://github.com/Jota-project/jota-gateway/commit/273d42c87a9ee0ddb9bc21499771b480d1b0eff2))
* **main:** build OrchestratorRegistry in lifespan, register openai_router ([827bd1c](https://github.com/Jota-project/jota-gateway/commit/827bd1c249362b5617ba0ceffb36159b6a5cc53d))
* **monitoring:** add PipelineEvent and PipelineTracker ([b574ec7](https://github.com/Jota-project/jota-gateway/commit/b574ec794e04d5efaacfa82f8b50a4e4ec8198d8))
* **monitoring:** add SessionRecord and SessionRegistry ([61804ae](https://github.com/Jota-project/jota-gateway/commit/61804aec34a23807aa8aa337a1d1a8a3ca3060ac))
* **monitoring:** add sessions REST API and wire SessionRegistry into lifespan ([c4c2ae7](https://github.com/Jota-project/jota-gateway/commit/c4c2ae77a9d64b25b67c7af10c2b1e521d82e4a7))
* **monitoring:** instrument JotaBridge with PipelineTracker ([ec02ad9](https://github.com/Jota-project/jota-gateway/commit/ec02ad9b96384c1b713b7c2dcd1ce9f882b936cc))
* openai endpoint always uses orchestrator, add HTTP pipeline tracking, remove LLM bypass ([f402ded](https://github.com/Jota-project/jota-gateway/commit/f402dede9ba26d89bf688f72ecaad066982afb05))
* OPENCLAW_SESSION_KEY configurable via .env ([2a4a5c2](https://github.com/Jota-project/jota-gateway/commit/2a4a5c2cab2d17174a040b028343b25ddb719a01))
* **openclaw:** add on_disconnect callback and guard stream send ([022377f](https://github.com/Jota-project/jota-gateway/commit/022377f21abc89a7884d313522f0eed217a32526))
* **orchestrators:** add OpenClawClient (WebSocket, protocol v4) ([8cfca7d](https://github.com/Jota-project/jota-gateway/commit/8cfca7db774ac32b64f050500439be16070b853e))
* **orchestrators:** add OrchestratorEvent and OrchestratorProtocol ([c423d48](https://github.com/Jota-project/jota-gateway/commit/c423d48134b2dd65c2b7cf2bba44184e140ed261))
* **orchestrators:** add OrchestratorRegistry and build_registry ([5356bd2](https://github.com/Jota-project/jota-gateway/commit/5356bd2fc044dd67ed8113af90ad1ee26b8a60e0))
* **orchestrators:** add ReconnectingOrchestrator wrapper with state machine ([3837926](https://github.com/Jota-project/jota-gateway/commit/3837926192912e2dd5cd21b30422d07b99d61dbc))
* **registry:** wrap orchestrators in ReconnectingOrchestrator, add get_status/reconnect ([fc2f0a1](https://github.com/Jota-project/jota-gateway/commit/fc2f0a1aa0b5b228394eacaa4c8b68adfd32e58e))
* **routes:** inject orchestrator from registry into JotaBridge ([351a049](https://github.com/Jota-project/jota-gateway/commit/351a049f01ff64abf1210d6936378d79df3c5ee8))
* WS/HTTP coherence — shared session key, call_orchestrator, remove LLM bypass ([#53](https://github.com/Jota-project/jota-gateway/issues/53)) ([237ae52](https://github.com/Jota-project/jota-gateway/commit/237ae52fd2c1e9a6117d686f9972d3e670fbe391))

## [1.5.2](https://github.com/Jota-project/jota-gateway/compare/v1.5.1...v1.5.2) (2026-04-08)


### Bug Fixes

* **gateway:** orchestrator not reached due to TTS failure and premature session teardown ([#36](https://github.com/Jota-project/jota-gateway/issues/36)) ([69d7bbf](https://github.com/Jota-project/jota-gateway/commit/69d7bbfdd77c1a0ae8b2d40d6a9ac734863aecd2))
* **gateway:** TTS failure no longer blocks orchestrator, transcriber clean close not treated as drop ([51a74af](https://github.com/Jota-project/jota-gateway/commit/51a74af8ae622b254432fd21176a7a4513431d76))

## [1.5.1](https://github.com/Jota-project/jota-gateway/compare/v1.5.0...v1.5.1) (2026-04-06)


### Bug Fixes

* **ci:** add ruff to requirements.txt so lint step finds the binary ([c24ff19](https://github.com/Jota-project/jota-gateway/commit/c24ff19cdc0940333b1cda012423682cad539d4b))
* **ci:** set PYTHONPATH=. so pytest can resolve the src package ([df84c5d](https://github.com/Jota-project/jota-gateway/commit/df84c5d3642043ff7db462944c678b6c07bdf791))

# [1.5.0](https://github.com/Jota-project/jota-gateway/compare/v1.4.0...v1.5.0) (2026-04-05)


### Features

* Fase 3 — propagate ClientConfig to TTS, Orchestrator, and barge-in ([#34](https://github.com/Jota-project/jota-gateway/issues/34)) ([4a490ba](https://github.com/Jota-project/jota-gateway/commit/4a490bac49ef5fa8f1a7e9a94eb66d5d63a5ffc7)), closes [#8](https://github.com/Jota-project/jota-gateway/issues/8)
* OrchestratorClient passes system_prompt_extra in payload ([fc275fb](https://github.com/Jota-project/jota-gateway/commit/fc275fb6735d7eabb45d93b978de75b7acbb7bec))
* propagate ClientConfig to TTS, Orchestrator, and barge-in threshold ([2532a20](https://github.com/Jota-project/jota-gateway/commit/2532a20b3c7403db13b54b46b8bdca3b3fd9c766))
* TTSClient.connect() accepts optional voice and speed params ([fe390ac](https://github.com/Jota-project/jota-gateway/commit/fe390acb89b941782645670d4198f39fb5a61fb2))

# [1.4.0](https://github.com/Jota-project/jota-gateway/compare/v1.3.0...v1.4.0) (2026-04-03)


### Features

* DELETE /api/conversations/{id} — archivar conversación ([#32](https://github.com/Jota-project/jota-gateway/issues/32)) ([4405d74](https://github.com/Jota-project/jota-gateway/commit/4405d741cac51c0e8edb225224db9fede436a871)), closes [#24](https://github.com/Jota-project/jota-gateway/issues/24)
* DELETE /api/conversations/{id} — archivar conversación (closes [#24](https://github.com/Jota-project/jota-gateway/issues/24)) ([0a6fd5d](https://github.com/Jota-project/jota-gateway/commit/0a6fd5de5dab35b0219aaa1c3ab9b4ad9df45ed2))

# [1.3.0](https://github.com/Jota-project/jota-gateway/compare/v1.2.1...v1.3.0) (2026-04-03)


### Features

* add make_cache() utility in src/core/cache.py ([209e50e](https://github.com/Jota-project/jota-gateway/commit/209e50e830b3068c188375334f60ce94f28291f5))
* cache get_models() with 300s TTL ([d987650](https://github.com/Jota-project/jota-gateway/commit/d987650ecd4ce5b6fa4714d7c1a63d2bb72e2f8a))
* cache get_session() with 60s TTL — reduces jota-db round-trips (closes [#23](https://github.com/Jota-project/jota-gateway/issues/23), closes [#22](https://github.com/Jota-project/jota-gateway/issues/22)) ([49c2a3d](https://github.com/Jota-project/jota-gateway/commit/49c2a3d4db17098c52ee34bcfe221b5903824ae2))
* in-memory TTL cache for get_session() and get_models() ([#33](https://github.com/Jota-project/jota-gateway/issues/33)) ([e5e1f7f](https://github.com/Jota-project/jota-gateway/commit/e5e1f7f044ed973963e2bd792b15945e7c2d0b20)), closes [#23](https://github.com/Jota-project/jota-gateway/issues/23) [#22](https://github.com/Jota-project/jota-gateway/issues/22)

## [1.2.1](https://github.com/Jota-project/jota-gateway/compare/v1.2.0...v1.2.1) (2026-04-03)


### Bug Fixes

* send x-client-id header to orchestrator ([#31](https://github.com/Jota-project/jota-gateway/issues/31)) ([799c96a](https://github.com/Jota-project/jota-gateway/commit/799c96ac51ff1383e11a48dd9b3421f051ea35d9)), closes [#20](https://github.com/Jota-project/jota-gateway/issues/20)
* send x-client-id header to orchestrator (closes [#20](https://github.com/Jota-project/jota-gateway/issues/20)) ([d432f7f](https://github.com/Jota-project/jota-gateway/commit/d432f7f9c6d7ad6eba548903a581e9e433244f51))

# [1.2.0](https://github.com/Jota-project/jota-gateway/compare/v1.1.0...v1.2.0) (2026-04-03)


### Features

* add config_routes — GET/PUT /api/config, POST /api/config/reset ([2c23b37](https://github.com/Jota-project/jota-gateway/commit/2c23b3776cc4c553b173a88b0da70b2f5c1fab8a))
* add conversation_routes — GET /api/conversations and /messages ([a1f67e7](https://github.com/Jota-project/jota-gateway/commit/a1f67e787ee7033826a0795984baae2d8a7231ba))
* add get_verified_client dependency for REST API auth ([4125fde](https://github.com/Jota-project/jota-gateway/commit/4125fde2f84e62e39b7286e7e4a2cf068b19bcfd))
* add health_routes — GET /api/health with parallel service pings ([045c793](https://github.com/Jota-project/jota-gateway/commit/045c79352ed0b2fab8aa3e5a8a37753eee4828e5))
* add models_routes — GET /api/models ([a3d2a68](https://github.com/Jota-project/jota-gateway/commit/a3d2a68595aab64b4b51c42f0109915b78d9ad82))
* Fase 2 — REST API pública (config, conversations, models, health) ([#29](https://github.com/Jota-project/jota-gateway/issues/29)) ([3911f88](https://github.com/Jota-project/jota-gateway/commit/3911f8853bb927e57207a9b9d1ddcd200d1802fa)), closes [#26](https://github.com/Jota-project/jota-gateway/issues/26) [#25](https://github.com/Jota-project/jota-gateway/issues/25) [#1](https://github.com/Jota-project/jota-gateway/issues/1)
* mount REST API routers — config, conversations, models, health (Fase 2, closes [#1](https://github.com/Jota-project/jota-gateway/issues/1)) ([9185995](https://github.com/Jota-project/jota-gateway/commit/91859958c8f6699572627f6abc9d4af4a1958c90))

# [1.1.0](https://github.com/Jota-project/jota-gateway/compare/v1.0.0...v1.1.0) (2026-04-03)


### Bug Fixes

* get_messages passes X-Client-Id for ownership validation; add get_models() ([272ea5b](https://github.com/Jota-project/jota-gateway/commit/272ea5bbe97fbdc5b36dcc5fac063406aeb58991))


### Features

* add TranscriberClient.ping() static method for health checks ([4727905](https://github.com/Jota-project/jota-gateway/commit/47279054d9b6b6b8d6283ace851f44c026c3197e))

# 1.0.0 (2026-04-03)


### Bug Fixes

* deduplicate final transcriptions before calling orchestrator ([2245d6a](https://github.com/Jota-project/jota-gateway/commit/2245d6afca334ad897c1753084d75975de90e8f6)), closes [Jota-project/jota-transcriber#27](https://github.com/Jota-project/jota-transcriber/issues/27)
* deduplicate final transcriptions before calling orchestrator ([#7](https://github.com/Jota-project/jota-gateway/issues/7)) ([7645a39](https://github.com/Jota-project/jota-gateway/commit/7645a3978e94b78f61f916b1488c63c7a89d8a73)), closes [#3](https://github.com/Jota-project/jota-gateway/issues/3) [Jota-project/jota-transcriber#27](https://github.com/Jota-project/jota-transcriber/issues/27)
* forward partial transcriptions to client in real time ([dccca82](https://github.com/Jota-project/jota-gateway/commit/dccca82728e5e17530fa7a8abece9b03284b1b57))
* forward partial transcriptions to client in real time ([#16](https://github.com/Jota-project/jota-gateway/issues/16)) ([72abc85](https://github.com/Jota-project/jota-gateway/commit/72abc853506c2d5b2244b94744324fec5558982e)), closes [#4](https://github.com/Jota-project/jota-gateway/issues/4)
* guard all client_ws sends in _call_orchestrator against disconnect ([9486f60](https://github.com/Jota-project/jota-gateway/commit/9486f60acfec9fcdcaa563e2fefd953f65c32ac5))
* guard ping() against uninitialized _http + add logging ([3c244c9](https://github.com/Jota-project/jota-gateway/commit/3c244c9fb0d26dc3a9cdc0d3b256746cf6da64c0))
* handle raw websocket.disconnect in _client_input_loop ([bcfc0d4](https://github.com/Jota-project/jota-gateway/commit/bcfc0d49dfe541a84aa2f6d1bb7faee465ce8a7f))
* protocolo del transcriber — code, session_id, buffer_full, TranscriberConfig ([6faaf9f](https://github.com/Jota-project/jota-gateway/commit/6faaf9fc210a0a3ac0fbd8ccaf29b585bafee9a8)), closes [#9](https://github.com/Jota-project/jota-gateway/issues/9) [#10](https://github.com/Jota-project/jota-gateway/issues/10) [#11](https://github.com/Jota-project/jota-gateway/issues/11) [#12](https://github.com/Jota-project/jota-gateway/issues/12)
* protocolo del transcriber — code, session_id, buffer_full, TranscriberConfig ([#19](https://github.com/Jota-project/jota-gateway/issues/19)) ([12279d1](https://github.com/Jota-project/jota-gateway/commit/12279d1e0fb1c7a40ce64003775584bede9d302e)), closes [#18](https://github.com/Jota-project/jota-gateway/issues/18) [#9](https://github.com/Jota-project/jota-gateway/issues/9) [#10](https://github.com/Jota-project/jota-gateway/issues/10) [#11](https://github.com/Jota-project/jota-gateway/issues/11) [#12](https://github.com/Jota-project/jota-gateway/issues/12) [#14](https://github.com/Jota-project/jota-gateway/issues/14)
* separate send_json guard from tts.send_text_chunk in _on_token ([cdac053](https://github.com/Jota-project/jota-gateway/commit/cdac0530943197fd2bc38e3d608d17c1a77746ca))
* update listen_loop call site for new callback signature (interim) ([94a2df8](https://github.com/Jota-project/jota-gateway/commit/94a2df85c5f0e564c2d3f8e953d1ca8ac8bb2dcb))


### Features

* add _active_turn and _cancel_active_turn() to JotaBridge ([941bac1](https://github.com/Jota-project/jota-gateway/commit/941bac12e6692104596647677ad228763bc4942c))
* add _on_transcription() with barge-in and replace _on_transcribed_text ([d80687f](https://github.com/Jota-project/jota-gateway/commit/d80687f62b7571827ad73bd93f4a389fa5d32c9b))
* add BARGE_IN_MIN_CHARS config setting ([541ae18](https://github.com/Jota-project/jota-gateway/commit/541ae1860b96fcf9e61ca81f8aac8dabe2bd1ddc))
* add JotaBridge.health_check() ([419a257](https://github.com/Jota-project/jota-gateway/commit/419a257c49a6b55bae89cb6718858bb2af55dd72))
* add OrchestratorClient.ping() ([09f0812](https://github.com/Jota-project/jota-gateway/commit/09f0812d8eddcb9c0576779f9dfa321fca7b6bbe))
* add TTSClient.ping() static method ([1fd4a03](https://github.com/Jota-project/jota-gateway/commit/1fd4a03a439e5e499b7705c5f96989d54fcbf5fb))
* close_all() awaits _active_turn before closing other clients ([590d173](https://github.com/Jota-project/jota-gateway/commit/590d173f34442af9d6b00bf6c6e7b836682689e0))
* Fase 1 — DbClient + resolver identidad en el handshake ([#18](https://github.com/Jota-project/jota-gateway/issues/18)) ([9d2a51e](https://github.com/Jota-project/jota-gateway/commit/9d2a51e7ca101c6caa4f2a645aaf55ac71ead1bd)), closes [#2](https://github.com/Jota-project/jota-gateway/issues/2) [#13](https://github.com/Jota-project/jota-gateway/issues/13) [#15](https://github.com/Jota-project/jota-gateway/issues/15)
* listen_loop forwards (text, is_final) to callback ([8a5ac9a](https://github.com/Jota-project/jota-gateway/commit/8a5ac9a60c21dc4d4b25d7cfb9d2c4c268e4d6bd))
* notify client when transcriber drops or goes silent ([2df24fc](https://github.com/Jota-project/jota-gateway/commit/2df24fc4b209b3591e2de33840fbb8231181d915))
* notify client when transcriber drops or goes silent ([#6](https://github.com/Jota-project/jota-gateway/issues/6)) ([67a17be](https://github.com/Jota-project/jota-gateway/commit/67a17be5a3d1b31775913cc1a45a5be89f02face))
* resolver client_key → Client+ClientConfig en el handshake (Fase 1 DbClient) ([08cd681](https://github.com/Jota-project/jota-gateway/commit/08cd68196de952bf1e6f0a87c5310aad26306507)), closes [#2](https://github.com/Jota-project/jota-gateway/issues/2) [#13](https://github.com/Jota-project/jota-gateway/issues/13) [#15](https://github.com/Jota-project/jota-gateway/issues/15)
* route client_key through handshake to all downstream services ([5ee17ee](https://github.com/Jota-project/jota-gateway/commit/5ee17eedd7183877a9aad228f4b8100e0961d72f)), closes [#5](https://github.com/Jota-project/jota-gateway/issues/5) [#2](https://github.com/Jota-project/jota-gateway/issues/2)
* route client_key through handshake to all downstream services ([#17](https://github.com/Jota-project/jota-gateway/issues/17)) ([8bc4724](https://github.com/Jota-project/jota-gateway/commit/8bc47244b6bfbf534bca76e9702de49376405470)), closes [#5](https://github.com/Jota-project/jota-gateway/issues/5) [#2](https://github.com/Jota-project/jota-gateway/issues/2) [#2](https://github.com/Jota-project/jota-gateway/issues/2)
* wire health_check() into gateway session startup ([fcaf2fd](https://github.com/Jota-project/jota-gateway/commit/fcaf2fd19511844f8aef7dae987b9d28fe502288))
