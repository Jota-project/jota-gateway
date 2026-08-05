import json
import logging
from typing import AsyncGenerator, Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


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
        self.ws = None

    async def connect(
        self,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
    ) -> None:
        """Open WS and authenticate. Raises RuntimeError on auth failure."""
        ws_url = f"ws://{self.url}/ws"
        self.ws = await websockets.connect(ws_url)
        auth_err: Optional[Exception] = None
        try:
            auth_msg: dict = {"type": "auth", "token": self.token}
            if voice is not None:
                auth_msg["voice"] = voice
            if speed is not None:
                auth_msg["speed"] = speed
            await self.ws.send(json.dumps(auth_msg))
            try:
                raw = await self.ws.recv()
            except ConnectionClosed as exc:
                raise RuntimeError(
                    f"[{self.client_id}] TTS connection closed during auth: {exc}"
                ) from exc

            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as exc:
                raise RuntimeError(
                    f"[{self.client_id}] TTS sent non-JSON during auth: {raw!r}"
                ) from exc

            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"[{self.client_id}] TTS auth failed: {msg}")

            logger.info("[%s] Connected to TTS at ws://%s/ws", self.client_id, self.url)
        except Exception as exc:
            auth_err = exc
            raise
        finally:
            if auth_err is not None and self.ws is not None:
                try:
                    await self.ws.close(1000)
                except Exception:
                    pass
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
        """
        if not self.ws:
            return
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    yield msg
                else:
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.warning("[%s] TTS unparseable frame: %r", self.client_id, msg)
                        continue
                    msg_type = data.get("type")
                    if msg_type == "done":
                        break
                    elif msg_type == "error":
                        logger.warning(
                            "[%s] TTS error: %s — %s",
                            self.client_id, data.get("code"), data.get("message"),
                        )
                        break
                    else:
                        logger.debug("[%s] TTS control frame: %s", self.client_id, msg_type)
        except ConnectionClosed:
            logger.info("[%s] TTS audio stream ended (ConnectionClosed)", self.client_id)

    async def close(self) -> None:
        """Close the WS with code 1000. No-op if ws is None."""
        if self.ws is None:
            return
        try:
            await self.ws.close(1000)
        except Exception:
            pass

    @staticmethod
    async def ping(url: str) -> bool:
        """Return True if the TTS /ready endpoint responds with 2xx.

        Unlike /health (pure liveness, always 200 if the process is up),
        /ready reflects real engine readiness (503 "not_ready" while a TTS
        engine isn't loaded) — see issue #101.

        Expects url as host:port (no protocol, no path).
        Empty URLs return False.
        """
        if not url:
            return False
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"http://{url}/ready", timeout=5.0)
                return r.is_success
        except Exception:
            return False
