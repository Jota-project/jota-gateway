import asyncio
import logging
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect

from src.core.config import settings
from src.models.schemas import Handshake
from src.services.orchestrator_client import OrchestratorClient
from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient

logger = logging.getLogger(__name__)

class JotaBridge:
    """
    Titiritero principal. Gestiona la conexión de un cliente físico
    y enruta asincrónicamente los mensajes utilizando los adaptadores de microservicio.
    """
    def __init__(self, client_id: str, client_ws: WebSocket):
        self.client_id = client_id
        self.client_ws = client_ws
        self.handshake: Optional[Handshake] = None

        # Microservicios Clients
        self.orchestrator: Optional[OrchestratorClient] = None
        self.transcriber: Optional[TranscriberClient] = None

        self.tasks: list[asyncio.Task] = []
        self._active_turn: Optional[asyncio.Task] = None

    async def connect_internal_services(self):
        """Inicializa clientes de microservicios dependiendo del handshake."""
        connect_tasks = []

        # 1. Orchestrator — siempre activo (es el cerebro)
        self.orchestrator = OrchestratorClient(
            base_url=settings.ORCHESTRATOR_BASE_URL,
            api_key=settings.ORCHESTRATOR_API_KEY,
            client_id=self.client_id,
        )
        connect_tasks.append(self.orchestrator.connect())

        # 2. Transcriber (solo si el dispositivo mandará audio)
        if self.handshake.input_mode == "audio":
            self.transcriber = TranscriberClient(
                url=settings.TRANSCRIBER_WS_URL,
                client_id=self.client_id
            )
            connect_tasks.append(self.transcriber.connect(language="es"))

        await asyncio.gather(*connect_tasks)

    async def close_all(self):
        # Cancel and await the active turn first so TTS finally-blocks run before
        # orchestrator/transcriber clients are closed.
        if self._active_turn and not self._active_turn.done():
            self._active_turn.cancel()
            try:
                await self._active_turn
            except (asyncio.CancelledError, Exception):
                pass

        for task in self.tasks:
            if not task.done():
                task.cancel()

        close_aws = []
        if self.orchestrator: close_aws.append(self.orchestrator.close())
        if self.transcriber: close_aws.append(self.transcriber.close())

        if close_aws:
            await asyncio.gather(*close_aws, return_exceptions=True)

        logger.info(f"[{self.client_id}] Puente asíncrono cerrado.")

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

    async def run(self):
        # Loop principal de lectura del cliente
        self.tasks.append(asyncio.create_task(self._client_input_loop()))

        # Loop del Transcriptor (solo si hay audio de entrada)
        if self.transcriber:
            self.tasks.append(asyncio.create_task(
                self.transcriber.listen_loop(on_transcription_callback=self._on_transcription)
            ))

        try:
            done, pending = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try: task.result()
                except asyncio.CancelledError: pass
                except Exception as e: logger.error(f"[{self.client_id}] Loop crasheó: {e}")
        finally:
            await self.close_all()

    async def _client_input_loop(self):
        """Atrapa entradas del cliente (Micrófono o Texto)."""
        try:
            while True:
                message = await self.client_ws.receive()

                # AUDIO → Transcriber
                if "bytes" in message:
                    if self.handshake.input_mode == "audio" and self.transcriber:
                        await self.transcriber.send_audio(message["bytes"])

                # TEXTO → Orchestrator (prompt directo desde un cliente de texto)
                elif "text" in message:
                    text_data = message["text"]
                    if self.orchestrator:
                        await self._call_orchestrator(text_data)

        except WebSocketDisconnect:
            logger.info(f"[{self.client_id}] Cliente físico desconectado.")
        except Exception as e:
            logger.error(f"[{self.client_id}] Error en input loop: {e}")

    async def _on_transcription(self, text: str, is_final: bool):
        """Callback dispatched by TranscriberClient on every transcription event.

        Partials: trigger barge-in if text is substantial and a turn is active.
        Finals: cancel any running turn, notify the client, start a new turn.
        All client_ws sends are guarded — the client may disconnect at any time.
        """
        if not is_final:
            # Barge-in: interrupt active turn if partial is substantial enough
            if len(text) >= settings.BARGE_IN_MIN_CHARS:
                if await self._cancel_active_turn():
                    logger.info(f"[{self.client_id}] Barge-in: turno cancelado por parcial '{text[:30]}'")
                    try:
                        await self.client_ws.send_json({"type": "interrupted"})
                    except Exception:
                        pass
            return  # partials never reach the orchestrator

        # Final: cancel any running turn, notify client, start new turn
        await self._cancel_active_turn()
        logger.info(f"[{self.client_id}] Transcripción final: '{text}'")
        try:
            await self.client_ws.send_json({"type": "transcription", "text": text})
        except Exception:
            return  # client disconnected — no point starting a new turn
        self._active_turn = asyncio.create_task(self._call_orchestrator(text))

    async def _call_orchestrator(self, text: str):
        """
        Envía texto al Orchestrator y despacha tokens/eventos al cliente y al TTS.

        Si el cliente pidió audio, crea un TTSClient por petición y corre
        pipe_tokens + pipe_audio concurrentemente vía asyncio.gather.
        """
        needs_audio = "audio" in self.handshake.output_mode

        tts: Optional[TTSClient] = None
        if needs_audio:
            tts = TTSClient(
                url=settings.TTS_WS_URL,
                token=settings.TTS_TOKEN,
                client_id=self.client_id,
            )
            await tts.connect()

        async def _on_token(token_text: str):
            if "text" in self.handshake.output_mode:
                await self.client_ws.send_json({"type": "token", "content": token_text})
            if tts:
                await tts.send_text_chunk(token_text)

        async def _on_event(data: dict):
            if data.get("type") == "error" or "status" in self.handshake.output_mode:
                await self.client_ws.send_json(data)

        async def pipe_tokens():
            await self.orchestrator.listen_loop(
                text=text,
                on_token=_on_token,
                on_event=_on_event,
            )
            if tts:
                await tts.end()

        async def pipe_audio():
            async for chunk in tts.get_audio_stream():
                await self.client_ws.send_bytes(chunk)

        if tts:
            try:
                await asyncio.gather(pipe_tokens(), pipe_audio())
            finally:
                await tts.close()
        else:
            await pipe_tokens()
