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
