import asyncio
import json
import logging
from collections.abc import AsyncGenerator

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from jota_gateway.core.config import settings
from jota_gateway.services.reconnection import describe_error, ping_ready, safe_code

logger = logging.getLogger(__name__)

# shortcut: con un TTS más rápido que 1x, una respuesta de más de ~3,5 min llena la cola y se corta la síntesis; subir el tope o frenar la lectura si hace falta
MAX_BUFFER_BYTES = 10 * 1024 * 1024


class TTSHandshakeError(RuntimeError):
    """The auth exchange failed after the socket opened: closed, non-JSON or non-object frame."""


class TTSAuthError(TTSHandshakeError):
    """The TTS answered `auth` with something other than `auth_ok` (jota-speaker: `auth_error`)."""


class TTSClient:
    """
    Client for jota-speaker TTS service (port 8005).

    One instance = one WebSocket session: auth → tokens → end → audio → done.
    Create a fresh instance per _call_orchestrator invocation.
    """

    def __init__(self, url: str, token: str, client_id: str) -> None:
        self.url = url
        self.token = token
        self.client_id = client_id
        self.ws: ClientConnection | None = None
        self._out: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._buffered = 0
        self._reader: asyncio.Task[None] | None = None

    async def connect(
        self,
        voice: str | None = None,
        speed: float | None = None,
    ) -> None:
        """Open WS and authenticate. Raises TTSHandshakeError (a RuntimeError) on auth failure;
        TTSAuthError when the service refused the token."""
        ws_url = f"ws://{self.url}/ws"
        _authenticated = False
        try:
            ws = await websockets.connect(ws_url)
            self.ws = ws
            auth_msg: dict = {"type": "auth", "token": self.token}
            if voice is not None:
                auth_msg["voice"] = voice
            if speed is not None:
                auth_msg["speed"] = speed
            await ws.send(json.dumps(auth_msg))
            try:
                raw = await asyncio.wait_for(
                    ws.recv(),
                    timeout=settings.TTS_AUTH_TIMEOUT_S,
                )
            except ConnectionClosed as exc:
                # Sin `exc`: el motivo de cierre lo pone el servidor (#238).
                close_code = exc.rcvd.code if exc.rcvd else "none"
                raise TTSHandshakeError(
                    f"[{self.client_id}] TTS connection closed during auth (code={close_code})"
                ) from exc

            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as exc:
                # G-40 (#216): never the frame content, only a fixed reason.
                raise TTSHandshakeError(f"[{self.client_id}] TTS sent non-JSON during auth") from exc

            if not isinstance(msg, dict):
                # G-66 (#214): valid JSON but not an object — name the type only.
                raise TTSHandshakeError(
                    f"[{self.client_id}] TTS sent non-object during auth ({type(msg).__name__})"
                )

            if msg.get("type") != "auth_ok":
                # G-40 (#216): only the protocol error `code` (short identifier), never
                # the rest of the message.
                code = safe_code(msg.get("code"))
                raise TTSAuthError(f"[{self.client_id}] TTS auth failed (code={code})")

            _authenticated = True
            logger.info("[%s] Connected to TTS at ws://%s/ws", self.client_id, self.url)
        except asyncio.CancelledError:
            raise
        finally:
            if not _authenticated and self.ws is not None:
                try:
                    await self.ws.close(1000)
                except Exception as e:  # noqa: BLE001 — limpieza tras un connect fallido
                    logger.debug(
                        "[%s] TTS close after failed connect: %s", self.client_id, describe_error(e)
                    )
                finally:
                    self.ws = None

    async def send_text_chunk(self, text: str) -> None:
        """Send one LLM token. No-op if WS is unavailable."""
        if not self.ws:
            return
        try:
            await self.ws.send(json.dumps({"type": "token", "text": text}))
        except ConnectionClosed:
            logger.warning("[%s] send_text_chunk: ConnectionClosed", self.client_id)

    async def end(self) -> None:
        """Signal no more tokens. No-op if WS is unavailable."""
        if not self.ws:
            return
        try:
            await self.ws.send(json.dumps({"type": "end"}))
        except ConnectionClosed:
            logger.warning("[%s] end: ConnectionClosed", self.client_id)

    async def get_audio_stream(self) -> AsyncGenerator[bytes, None]:
        """
        Yields binary PCM16 audio frames. Skips JSON control messages.
        Stops on 'done', 'error', or ConnectionClosed.
        El socket se lee por delante en `_read` (el consumidor va a ritmo de reproducción).
        """
        if not self.ws:
            return
        if self._reader is None:
            self._reader = asyncio.get_running_loop().create_task(self._read(self.ws))
        while True:
            item = await self._out.get()
            if item is None:
                self._out.put_nowait(None)  # otra iteración posterior también termina
                return
            self._buffered -= len(item)
            yield item

    async def _read(self, ws: ClientConnection) -> None:
        """Vuelca el audio a la cola; siempre deja un `None` final para terminar el iterador."""
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    if self._buffered + len(msg) > MAX_BUFFER_BYTES:
                        logger.warning(
                            "[%s] TTS: el búfer de audio supera %d bytes; se corta la síntesis",
                            self.client_id,
                            MAX_BUFFER_BYTES,
                        )
                        return
                    self._buffered += len(msg)
                    self._out.put_nowait(msg)
                    continue
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    logger.warning("[%s] TTS unparseable frame descartado (non-JSON)", self.client_id)
                    continue
                if not isinstance(data, dict):
                    # G-66 (#214): skip it; log the type only, never the content.
                    logger.warning(
                        "[%s] TTS frame descartado: no es un objeto JSON (%s)",
                        self.client_id,
                        type(data).__name__,
                    )
                    continue
                msg_type = data.get("type")
                if msg_type == "done":
                    return
                if msg_type == "error":
                    logger.warning("[%s] TTS error: %s", self.client_id, safe_code(data.get("code")))
                    return
                logger.debug("[%s] TTS control frame: %s", self.client_id, msg_type)
        except ConnectionClosed:
            logger.info("[%s] TTS audio stream ended (ConnectionClosed)", self.client_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — frontera del lector: cierra el stream y registra el tipo, nunca el frame
            logger.warning("[%s] TTS lectura abortada (%s)", self.client_id, describe_error(e))
        finally:
            self._out.put_nowait(None)

    async def close(self) -> None:
        """Close the WS with code 1000. No-op if ws is None."""
        if self.ws is None:
            return
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
            await asyncio.wait({self._reader}, timeout=0.9)  # no traga una cancelación ajena
        try:
            await self.ws.close(1000)
        except Exception as e:  # noqa: BLE001 — limpieza: el fallo se registra
            logger.debug("[%s] TTS close failed: %s", self.client_id, describe_error(e))

    ping = staticmethod(ping_ready)  # GET /ready (503 "not_ready" sin motor cargado)
