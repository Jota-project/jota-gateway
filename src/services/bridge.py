import asyncio
import json
import logging
import time
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect

from src.core.config import settings
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.protocol import OrchestratorProtocol
from src.services.pipeline_tracker import PipelineTracker
from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient
from src.services.openclaw.registry import ClientRegistry

logger = logging.getLogger(__name__)

class JotaBridge:
    """
    Titiritero principal. Gestiona la conexión de un cliente físico
    y enruta asincrónicamente los mensajes utilizando los adaptadores de microservicio.
    """
    def __init__(
        self,
        client: Client,
        config: ClientConfig,
        client_ws: WebSocket,
        orchestrator: OrchestratorProtocol,
        tracker: PipelineTracker,
        handshake: Handshake,
        client_registry: ClientRegistry,
        default_agent: str,
    ):
        self.client = client
        self.config = config
        self.client_id = client.id  # nombre legible del cliente (hab_sito, jota_desktop…)
        self.client_ws = client_ws
        self.handshake: Handshake = handshake
        self.orchestrator: OrchestratorProtocol = orchestrator   # injected, not created here
        self.tracker: PipelineTracker = tracker
        self._client_registry = client_registry
        self._default_agent = default_agent
        self.transcriber: Optional[TranscriberClient] = None
        self._push_tts = None
        self._push_audio_task: Optional[asyncio.Task] = None

        self.tasks: list[asyncio.Task] = []
        self._active_turn: Optional[asyncio.Task] = None
        self._session_start: float = 0.0
        self._first_audio_at: Optional[float] = None
        self._last_final_text: Optional[str] = None

    async def connect_internal_services(self):
        """Inicializa clientes de microservicios dependiendo del handshake."""
        connect_tasks = []

        # Transcriber (solo si el dispositivo mandará audio)
        if self.handshake.input_mode == "audio":
            self.transcriber = TranscriberClient(
                url=settings.TRANSCRIBER_WS_URL,
                client_id=self.client_id
            )
            connect_tasks.append(self.transcriber.connect(
                language=self.config.stt_language,
                token=self.client.client_key,
                vad_thold=self.config.stt_vad_thold,
            ))

        if connect_tasks:
            await asyncio.gather(*connect_tasks)

        self._client_registry.register(self.client_id, self)

    async def close_all(self):
        self._client_registry.unregister(self.client_id)
        # Await (don't cancel) the active turn so the orchestrator response is
        # delivered before we tear down microservice clients.  Explicit cancellation
        # only happens via _cancel_active_turn() (barge-in) or task cancellation
        # from outside; close_all() itself should let the turn finish naturally.
        if self._active_turn and not self._active_turn.done():
            try:
                await self._active_turn
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[{self.client_id}] _active_turn falló: {e}")

        if self._push_audio_task and not self._push_audio_task.done():
            self._push_audio_task.cancel()
            try:
                await self._push_audio_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._push_tts:
            try:
                await self._push_tts.close()
            except Exception:
                pass
            self._push_tts = None

        for task in self.tasks:
            if not task.done():
                task.cancel()

        close_aws = []
        if self.transcriber:
            close_aws.append(self.transcriber.close())

        if close_aws:
            await asyncio.gather(*close_aws, return_exceptions=True)

        await self.tracker.close()

    async def health_check(self) -> bool:
        """Ping each microservice and notify the client of any issues.

        Returns True if the session can proceed, False if a critical service
        is unavailable (caller should close the WebSocket).
        """
        # Orchestrator — always critical
        if not await self.orchestrator.ping():
            await self.client_ws.send_json({
                "type": "service_status",
                "service": "orchestrator",
                "status": "unavailable",
                "message": "Orchestrator unavailable, closing session",
            })
            return False

        # Transcriber — critical only for audio input (defense-in-depth;
        # primary failure path is caught by connect_internal_services → routes.py)
        if self.handshake.input_mode == "audio":
            if not self.transcriber or not self.transcriber._is_ready:
                await self.client_ws.send_json({
                    "type": "service_status",
                    "service": "transcriber",
                    "status": "unavailable",
                    "message": "Transcriber unavailable, closing session",
                })
                return False

        # TTS — non-critical; session continues in degraded mode
        if "audio" in self.handshake.output_mode:
            if not await TTSClient.ping(settings.TTS_WS_URL):
                await self.client_ws.send_json({
                    "type": "service_status",
                    "service": "tts",
                    "status": "unavailable",
                    "message": "Audio output unavailable",
                })

        return True

    async def _cancel_active_turn(self) -> bool:
        """Cancel the active orchestrator turn if one is running. Returns True if cancelled."""
        if self._active_turn and not self._active_turn.done():
            self._active_turn.cancel()
            try:
                await self._active_turn
            except (asyncio.CancelledError, Exception):
                pass
            self._active_turn = None
            return True
        return False

    async def _transcription_watchdog(self):
        """Cierra la sesión si el transcriptor no emite nada en TRANSCRIBER_SILENCE_TIMEOUT_S segundos
        contados desde que el cliente empezó a enviar audio."""
        from src.core.config import settings
        timeout = settings.TRANSCRIBER_SILENCE_TIMEOUT_S

        # Esperar a que el cliente empiece a enviar audio antes de vigilar
        while self._first_audio_at is None:
            await asyncio.sleep(0.5)
            if not self.transcriber or not self.transcriber._is_ready:
                return

        while True:
            await asyncio.sleep(2)
            if not self.transcriber or not self.transcriber._is_ready:
                return

            last = self.transcriber._last_transcription_at
            elapsed = time.monotonic() - last if last else time.monotonic() - self._first_audio_at

            if elapsed > timeout:
                logger.warning(f"[{self.client_id}] Watchdog: {elapsed:.1f}s sin transcripción del transcriptor")
                try:
                    await self.client_ws.send_json({
                        "type": "service_status",
                        "service": "transcriber",
                        "status": "degraded",
                        "message": "No transcription received — check microphone or audio quality",
                    })
                except Exception:
                    pass
                return

    async def run(self):
        self._session_start = time.monotonic()
        await self.tracker.record(
            "session_start",
            input_mode=self.handshake.input_mode,
            output_mode=self.handshake.output_mode,
        )

        # Loop principal de lectura del cliente
        self.tasks.append(asyncio.create_task(self._client_input_loop()))

        # Loop del Transcriptor (solo si hay audio de entrada)
        if self.transcriber:
            self.tasks.append(asyncio.create_task(
                self.transcriber.listen_loop(
                    on_transcription_callback=self._on_transcription,
                    on_warning_callback=self._on_transcriber_warning,
                )
            ))
            self.tasks.append(asyncio.create_task(self._transcription_watchdog()))

        # El ciclo de vida de la sesión lo marca _client_input_loop.
        # listen_loop y watchdog corren en background y terminan solos sin cerrar la sesión.
        client_task = self.tasks[0]  # siempre el primero (ver arriba)
        try:
            await client_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[{self.client_id}] client_input_loop crasheó: {e}")
        finally:
            # Notificar al cliente si el transcriptor cayó inesperadamente
            # (no notificar si ya recibimos una transcripción final — el cierre es esperado)
            if self.transcriber and self.transcriber._dropped_unexpectedly and self._last_final_text is None:
                try:
                    await self.client_ws.send_json({
                        "type": "service_status",
                        "service": "transcriber",
                        "status": "unavailable",
                        "message": "Transcriber connection lost unexpectedly",
                    })
                except Exception:
                    pass
            await self.close_all()

    async def _client_input_loop(self):
        """Atrapa entradas del cliente (Micrófono o Texto)."""
        try:
            while True:
                message = await self.client_ws.receive()

                if message.get("type") == "websocket.disconnect":
                    logger.info(f"[{self.client_id}] Cliente físico desconectado.")
                    break

                # AUDIO → Transcriber
                if "bytes" in message:
                    if self.handshake.input_mode == "audio" and self.transcriber:
                        if self._first_audio_at is None:
                            self._first_audio_at = time.monotonic()
                        await self.transcriber.send_audio(message["bytes"])

                # TEXTO → Orchestrator (prompt directo desde un cliente de texto)
                elif "text" in message:
                    text_data = message["text"]
                    try:
                        json_msg = json.loads(text_data)
                    except Exception:
                        json_msg = None

                    if json_msg and json_msg.get("type") == "end":
                        # Cliente terminó de hablar → señalizar fin al transcriber
                        if self.handshake.input_mode == "audio" and self.transcriber:
                            await self.transcriber.send_end()

                    elif json_msg and json_msg.get("type") == "cancel":
                        # Cliente cancela el turn activo sin lanzar uno nuevo
                        await self._cancel_active_turn()

                    elif json_msg and json_msg.get("type") == "send":
                        # Cliente confirma/edita la transcripción y la envía al orquestador
                        text = json_msg.get("text", "").strip()
                        if text and self.orchestrator:
                            logger.info(f"[{self.client_id}] send recibido: '{text[:60]}'")
                            await self._cancel_active_turn()
                            self._active_turn = asyncio.create_task(self._call_orchestrator(text))

                    elif json_msg is None and self.handshake.input_mode == "text":
                        # Texto plano en modo texto — compatibilidad con clientes de texto puro
                        await self._cancel_active_turn()
                        self._active_turn = asyncio.create_task(self._call_orchestrator(text_data))

        except WebSocketDisconnect:
            logger.info(f"[{self.client_id}] Cliente físico desconectado.")
        except Exception as e:
            logger.error(f"[{self.client_id}] Error en input loop: {e}")

    async def _on_transcriber_warning(self, code: str, message: Optional[str]):
        """Reenvía warnings del transcriber al cliente (e.g. buffer_full)."""
        try:
            await self.client_ws.send_json({
                "type": "service_status",
                "service": "transcriber",
                "status": "warning",
                "code": code,
                "message": message or code,
            })
        except Exception:
            pass  # cliente desconectado

    async def _on_transcription(self, text: str, is_final: bool):
        """Callback dispatched by TranscriberClient on every transcription event.

        Partials: trigger barge-in if text is substantial and a turn is active.
        Finals: cancel any running turn, notify the client, start a new turn.
        All client_ws sends are guarded — the client may disconnect at any time.
        """
        if not is_final:
            # Forward partial to client for live display
            try:
                await self.client_ws.send_json({"type": "transcription_partial", "text": text})
            except Exception:
                return  # client disconnected
            await self.tracker.record("transcription_partial", text_len=len(text))

            # Barge-in: interrupt active turn if partial is substantial enough
            if len(text) >= self.config.barge_in_min_chars:
                if await self._cancel_active_turn():
                    logger.info(f"[{self.client_id}] Barge-in: turno cancelado por parcial '{text[:30]}'")
                    await self.tracker.record("barge_in")
                    try:
                        await self.client_ws.send_json({"type": "interrupted"})
                    except Exception:
                        pass
            return  # partials never reach the orchestrator

        # Final: deduplicate — transcriber may emit the same text more than once
        if text == self._last_final_text:
            logger.debug(f"[{self.client_id}] Transcripción final duplicada descartada: '{text[:40]}'")
            return
        self._last_final_text = text
        await self.tracker.record("transcription_final", text=text[:60])

        # Final: cancel any running turn, notify client.
        # El orquestador se llama cuando el cliente envíe {"type": "send", "text": "..."}.
        await self._cancel_active_turn()
        logger.info(f"[{self.client_id}] Transcripción final: '{text}'")
        try:
            await self.client_ws.send_json({"type": "transcription", "text": text})
        except Exception as e:
            logger.warning(f"[{self.client_id}] send_json(transcription) falló: {e}")

    async def _call_orchestrator(self, text: str):
        """
        Envía texto al Orchestrator y despacha tokens/eventos al cliente y al TTS.

        Si el cliente pidió audio, crea un TTSClient por petición y corre
        pipe_tokens + pipe_audio concurrentemente vía asyncio.gather.
        """
        from src.services.orchestration import call_orchestrator
        from src.core.session_key import make_session_key

        self.tracker.start_turn()

        needs_audio = "audio" in self.handshake.output_mode

        tts: Optional[TTSClient] = None
        if needs_audio:
            tts = TTSClient(
                url=settings.TTS_WS_URL,
                token=settings.TTS_TOKEN,
                client_id=self.client_id,
            )
            try:
                await tts.connect(
                    voice=self.config.tts_voice,
                    speed=self.config.tts_speed,
                )
                await self.tracker.record("tts_start", voice=self.config.tts_voice or "")
            except Exception as e:
                logger.warning(f"[{self.client_id}] TTS no disponible, continuando en modo texto: {e}")
                tts = None

        agent = self.handshake.agent or self._default_agent
        session_key = make_session_key(agent, self.client_id)

        async def _on_token(token_text: str):
            try:
                if "text" in self.handshake.output_mode:
                    await self.client_ws.send_json({"type": "token", "content": token_text})
            except Exception:
                pass
            if tts:
                await tts.send_text_chunk(token_text)

        async def pipe_tokens():
            try:
                await call_orchestrator(
                    self.orchestrator, text, session_key, self.client_id,
                    model_id=self.config.preferred_model_id,
                    system_prompt_extra=self.config.system_prompt_extra,
                    tracker=self.tracker,
                    on_token=_on_token,
                )
            except RuntimeError as e:
                try:
                    await self.client_ws.send_json({"type": "error", "content": str(e)})
                except Exception:
                    pass
            finally:
                if tts:
                    await tts.end()

        async def pipe_audio():
            _first_chunk = True
            async for chunk in tts.get_audio_stream():
                if _first_chunk:
                    await self.tracker.record("tts_first_chunk")
                    _first_chunk = False
                try:
                    await self.client_ws.send_bytes(chunk)
                except Exception:
                    return
            await self.tracker.record("tts_done")

        if tts:
            try:
                await asyncio.gather(pipe_tokens(), pipe_audio())
            finally:
                await tts.close()
        else:
            await pipe_tokens()

        try:
            await self.client_ws.send_json({"type": "done"})
        except Exception:
            pass

    async def on_push_turn_start(self, session_key: str) -> None:
        if "audio" not in self.handshake.output_mode:
            return
        tts = TTSClient(
            url=settings.TTS_WS_URL, token=settings.TTS_TOKEN, client_id=self.client_id
        )
        try:
            await tts.connect(voice=self.config.tts_voice, speed=self.config.tts_speed)
            self._push_tts = tts
        except Exception as e:
            logger.warning(f"[{self.client_id}] Push TTS unavailable: {e}")
            return

        async def _pipe_push_audio():
            async for chunk in tts.get_audio_stream():
                try:
                    await self.client_ws.send_bytes(chunk)
                except Exception:
                    return

        self._push_audio_task = asyncio.create_task(_pipe_push_audio())

    async def deliver_push(self, payload: dict) -> None:
        delta = payload.get("deltaText", "")
        if not delta:
            return
        if "text" in self.handshake.output_mode:
            try:
                await self.client_ws.send_json({"type": "push", "content": delta})
            except Exception:
                pass
        if self._push_tts:
            await self._push_tts.send_text_chunk(delta)

    async def on_push_turn_end(self, session_key: str) -> None:
        if self._push_tts:
            try:
                await self._push_tts.end()
            except Exception:
                pass
            if self._push_audio_task and not self._push_audio_task.done():
                try:
                    await self._push_audio_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._push_audio_task = None
            try:
                await self._push_tts.close()
            except Exception:
                pass
            self._push_tts = None
