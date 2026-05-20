"""
orchestrator_client.py
~~~~~~~~~~~~~~~~~~~~~~
Adaptador HTTP hacia el endpoint POST /api/quick del JotaOrchestrator.

El endpoint /quick es REST, no WebSocket:
  - Autenticación: header "x-client-key" (no query param).
  - Entrada:       JSON body { text, user_id, model_id }.
  - Salida:        NDJSON streaming — una línea JSON por token/evento.
"""
import json
import logging
import httpx

from typing import Optional, Callable, Awaitable, Dict, Any, AsyncIterator

logger = logging.getLogger(__name__)


class OrchestratorClient:
    """
    Cliente HTTP para el endpoint QUICK del JotaOrchestrator.

    Realiza POST a /api/quick con streaming NDJSON y expone los tokens
    y eventos a través de callbacks, manteniendo la misma interfaz que
    esperaba el bridge anterior.
    """

    def __init__(self, base_url: str, api_key: str, client_id: str, timeout: float = 30.0):
        """
        Args:
            base_url:   URL base HTTP del orquestador, e.g. "http://localhost:8000".
            api_key:    Clave de cliente que se envía en el header x-client-key.
            client_id:  Identificador del cliente (usado como user_id en el payload).
            timeout:    Segundos máximos esperando el inicio de la respuesta streaming.
        """
        self.base_url = f"http://{base_url.rstrip('/')}"
        self.api_key = api_key
        self.client_id = client_id
        self.timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        """
        Inicializa el cliente HTTP persistente.
        Llamar antes de usar send_text / stream_response.
        """
        self._http = httpx.AsyncClient(
            headers={"x-client-key": self.api_key, "x-client-id": self.client_id},
            timeout=httpx.Timeout(self.timeout, read=None),  # read=None → sin límite en streaming
        )
        logger.info(f"[{self.client_id}] OrchestratorClient listo → {self.base_url}/api/quick")

    async def close(self):
        """Cierra el cliente HTTP subyacente."""
        if self._http:
            await self._http.aclose()
            self._http = None
            logger.info(f"[{self.client_id}] OrchestratorClient cerrado.")

    async def ping(self) -> bool:
        """Return True if the orchestrator /health endpoint responds with 2xx."""
        if not self._http:
            return False
        try:
            r = await self._http.get(f"{self.base_url}/health", timeout=5.0)
            return r.is_success
        except Exception as e:
            logger.debug(f"[{self.client_id}] Orchestrator health check failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_response(
        self,
        text: str,
        user_id: Optional[str] = None,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Envía un prompt al orquestador y produce los eventos NDJSON conforme llegan.

        Yields:
            dict con al menos "type" (str) y opcionalmente "content" (str).
            Posibles tipos: "token", "status", "error".
        """
        if not self._http:
            raise RuntimeError("OrchestratorClient no está conectado. Llama a connect() primero.")

        payload = {
            "text": text,
            "user_id": user_id or self.client_id,
        }
        if model_id:
            payload["model_id"] = model_id
        if system_prompt_extra is not None:
            payload["system_prompt_extra"] = system_prompt_extra

        url = f"{self.base_url}/api/quick"

        try:
            async with self._http.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(f"[{self.client_id}] Línea NDJSON malformada: {line!r}")

        except httpx.HTTPStatusError as e:
            logger.error(f"[{self.client_id}] Orchestrator respondió {e.response.status_code}: {e}")
            yield {"type": "error", "content": f"Orchestrator HTTP {e.response.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"[{self.client_id}] Error de red al llamar al Orchestrator: {e}")
            yield {"type": "error", "content": f"Error de conexión al Orchestrator: {e}"}

    async def listen_loop(
        self,
        text: str,
        on_token: Callable[[str], Awaitable[None]],
        on_event: Callable[[Dict[str, Any]], Awaitable[None]],
        user_id: Optional[str] = None,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
    ):
        """
        Variante de alto nivel: lanza stream_response y despacha callbacks.

        Args:
            text:       Texto del usuario a enviar.
            on_token:   Callback para fragmentos de texto ("type": "token").
            on_event:   Callback para eventos estructurales ("type": "status" | "error" | ...).
            user_id:            user_id opcional para el payload.
            model_id:           model_id opcional para seleccionar modelo.
            system_prompt_extra: instrucciones extra opcionales para el system prompt.
        """
        async for event in self.stream_response(
            text,
            user_id=user_id,
            model_id=model_id,
            system_prompt_extra=system_prompt_extra,
        ):
            if event.get("type") == "token" and event.get("content") is not None:
                await on_token(event["content"])
            else:
                await on_event(event)
