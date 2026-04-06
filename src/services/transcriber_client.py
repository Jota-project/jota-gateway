import httpx
import json
import logging
import time
import websockets
from websockets.exceptions import ConnectionClosed
from typing import Optional, Callable, Awaitable

from src.models.schemas import TranscriberConfig, TranscriberMessage

logger = logging.getLogger(__name__)

class TranscriberClient:
    """
    Maneja la conexión asíncrona hacia el servicio JotaTranscriber (C++).
    Realiza el Handshake (config) y manda/recibe audio bidireccionalmente.
    """
    def __init__(self, url: str, client_id: str):
        self.url = url
        self.client_id = client_id
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._is_ready = False
        self._session_id: Optional[str] = None  # recibido en el mensaje "ready"
        self._dropped_unexpectedly: bool = False
        self._last_transcription_at: Optional[float] = None

    async def connect(self, language: str = "es", token: str = "", vad_thold: float = 0.0):
        """Abre el socket y manda el Handshake inicial de config."""
        try:
            self.ws = await websockets.connect(f"ws://{self.url}/api/stt")

            config = TranscriberConfig(language=language, token=token, vad_thold=vad_thold)
            logger.info(f"[{self.client_id}] Conectando a Transcriber (ws://{self.url}/api/stt) lang={language!r} vad={vad_thold}")
            await self.ws.send(config.model_dump_json())

            response = await self.ws.recv()
            data = json.loads(response)
            msg = TranscriberMessage(**data)

            if msg.type == "ready":
                self._is_ready = True
                self._session_id = msg.session_id
                logger.info(f"[{self.client_id}] Transcriber listo. session_id={self._session_id!r}")
            elif msg.type == "error":
                raise Exception(f"Transcriber auth/config error [{msg.code}]: {msg.message}")
            else:
                raise Exception(f"Respuesta inesperada del transcriber en handshake: {msg.type!r}")

        except Exception as e:
            logger.error(f"[{self.client_id}] Error conectando a Transcriber: {e}")
            raise

    async def send_audio(self, audio_bytes: bytes):
        """Envía ráfagas PCM crudas al transcriptor si está habilitado."""
        if not self._is_ready or not self.ws:
            return
            
        try:
            await self.ws.send(audio_bytes)
        except ConnectionClosed:
            self._is_ready = False
            logger.warning(f"[{self.client_id}] Intento de enviar audio a Transcriber cerrado.")

    async def listen_loop(
        self,
        on_transcription_callback: Callable[[str, bool], Awaitable[None]],
        on_warning_callback: Optional[Callable[[str, Optional[str]], Awaitable[None]]] = None,
    ):
        """
        Bucle que escucha mensajes del transcriber.

        Args:
            on_transcription_callback: llamado con (text, is_final) en cada transcripción.
            on_warning_callback: llamado con (code, message) cuando llega un warning.
                                 Si es None, los warnings solo se loguean.
        """
        if not self.ws:
            return

        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    t_msg = TranscriberMessage(**data)

                    if t_msg.type == "transcription" and t_msg.text:
                        self._last_transcription_at = time.monotonic()
                        logger.debug(f"[{self.client_id}] Transcripción: is_final={t_msg.is_final!r} text='{t_msg.text[:40]}'")
                        await on_transcription_callback(t_msg.text, bool(t_msg.is_final))

                    elif t_msg.type == "warning":
                        logger.warning(f"[{self.client_id}] Transcriber warning [{t_msg.code}]: {t_msg.message}")
                        if on_warning_callback:
                            await on_warning_callback(t_msg.code or "unknown", t_msg.message)

                    elif t_msg.type == "error":
                        logger.error(f"[{self.client_id}] Transcriber error [{t_msg.code}]: {t_msg.message}")

                except json.JSONDecodeError:
                    logger.warning(f"[{self.client_id}] Transcriber mandó un non-JSON: {message}")
        except ConnectionClosed:
            if self._is_ready:
                self._dropped_unexpectedly = True
            self._is_ready = False
            logger.info(f"[{self.client_id}] Loop de escucha del Transcriber finalizado.")

    async def send_end(self):
        """Señaliza al transcriber que el audio terminó, disparando la transcripción final."""
        if not self._is_ready or not self.ws:
            return
        try:
            await self.ws.send(json.dumps({"type": "end"}))
        except ConnectionClosed:
            self._is_ready = False

    @staticmethod
    async def ping(url: str) -> bool:
        """Return True if the transcriber HTTP /health responds with 2xx.

        Expects url as host:port (no protocol).
        """
        if not url:
            return False
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"http://{url}/health", timeout=5.0)
                return r.is_success
        except Exception:
            return False

    async def close(self):
        self._is_ready = False
        if self.ws and not self.ws.closed:
            await self.ws.close()
