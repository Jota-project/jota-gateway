import asyncio
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
        self._dropped_unexpectedly: bool = False
        self._last_transcription_at: Optional[float] = None

    async def connect(self, language: str = "es", token: str = ""):
        """Abre el socket y manda el Handshake inicial de config"""
        try:
            logger.info(f"[{self.client_id}] Conectado a Transcriber WS ({self.url})")

            handshake = {
                "type": "config",
                "language": language,
                "token": token,
                "vad_thold": 0.0,
            }
            logger.info(f"[{self.client_id}] Configuracion transcriber: {handshake}")

            self.ws = await websockets.connect(self.url)

            logger.info(f"[{self.client_id}] Enviando handshake...")
            await self.ws.send(json.dumps(handshake))
            logger.info(f"[{self.client_id}] Handshake enviado, esperando 'ready'...")
            # Esperamos el evento de {"type": "ready"}
            response = await self.ws.recv()
            try:
                data = json.loads(response)
                msg = TranscriberMessage(**data)
                if msg.type == "ready":
                    self._is_ready = True
                    logger.info(f"[{self.client_id}] Transcriber Listo para recibir audio (VAD/Lang Confirmed).")
                elif msg.type == "error":
                     raise Exception(f"Transcriber error on connect: {msg.message}")
            except Exception as e:
                logger.error(f"[{self.client_id}] Fallo interpretando el handshake de Transcriber: {e}")
                raise e
                
        except Exception as e:
            logger.error(f"[{self.client_id}] Error conectando a Transcriber: {e}")
            raise e

    async def send_audio(self, audio_bytes: bytes):
        """Envía ráfagas PCM crudas al transcriptor si está habilitado."""
        if not self._is_ready or not self.ws:
            return
            
        try:
            await self.ws.send(audio_bytes)
        except ConnectionClosed:
            self._is_ready = False
            logger.warning(f"[{self.client_id}] Intento de enviar audio a Transcriber cerrado.")

    async def listen_loop(self, on_transcription_callback: Callable[[str, bool], Awaitable[None]]):
        """
        Bucle que escucha transcripciones del C++ y ejecuta el callback con (text, is_final).
        El loop no interpreta is_final — solo reenvía lo que llega.
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
                        logger.debug(f"[{self.client_id}] Transcripción recibida: is_final={t_msg.is_final!r} text='{t_msg.text[:40]}'")
                        await on_transcription_callback(t_msg.text, bool(t_msg.is_final))

                    elif t_msg.type == "error":
                        logger.error(f"[{self.client_id}] Transcriber Runtime Error: {t_msg.message}")
                    elif t_msg.type == "warning":
                        pass  # Warning de buffer full

                except json.JSONDecodeError:
                    logger.warning(f"[{self.client_id}] Transcriber mandó un non-JSON: {message}")
        except ConnectionClosed:
            if self._is_ready:
                self._dropped_unexpectedly = True
            self._is_ready = False
            logger.info(f"[{self.client_id}] Loop de escucha del Transcriber finalizado.")

    async def transcribe_file(self, audio_bytes: bytes, language: str = "es") -> str:
        """
        Sube un archivo de audio completo (pcm_f32le) y espera la transcripción final pseudo-síncronamente.
        """
        try:
            # 1. Conexión y Handshake
            await self.connect(language=language)
            
            # 2. Envío de datos
            await self.send_audio(audio_bytes)
            
            # 3. Indicar fin de stream (Trigger C++ inference commit)
            await self.ws.send(json.dumps({"type": "end"}))
            
            # 4. Esperar la respuesta final
            final_text = ""
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    t_msg = TranscriberMessage(**data)
                    
                    if t_msg.type == "transcription" and t_msg.is_final:
                         final_text += (t_msg.text or "")
                         break
                    elif t_msg.type == "error":
                         raise Exception(f"Error desde transcriptor remoto: {t_msg.message}")
                except json.JSONDecodeError:
                    pass
                    
            return final_text
            
        except Exception as e:
            logger.error(f"[{self.client_id}] Error extrayendo transcripción de archivo: {e}")
            raise e
        finally:
            await self.close()

    async def send_end(self):
        """Señaliza al transcriber que el audio terminó, disparando la transcripción final."""
        if not self._is_ready or not self.ws:
            return
        try:
            await self.ws.send(json.dumps({"type": "end"}))
        except ConnectionClosed:
            self._is_ready = False

    async def close(self):
        self._is_ready = False
        if self.ws and not self.ws.closed:
            await self.ws.close()
