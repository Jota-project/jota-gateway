from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import httpx
import logging
import json

from src.models.schemas import Handshake
from src.services.bridge import JotaBridge
from src.services.db_client import db_client

logger = logging.getLogger(__name__)

router = APIRouter()

@router.websocket("/ws/stream")
async def gateway_websocket(websocket: WebSocket):
    await websocket.accept()

    # 1. FASE DE HANDSHAKE INITIAL
    try:
        raw_msg = await websocket.receive_text()
        msg_data = json.loads(raw_msg)
        handshake = Handshake(**msg_data)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"Payload inicial inválido de handshake. {e}")
        await websocket.close(code=1008, reason="Handshake invalido. Se esperaba JSON de configuración.")
        return
    except WebSocketDisconnect:
        logger.info("Cliente desconectado antes o durante el handshake.")
        return

    # 2. RESOLVER IDENTIDAD EN JOTA-DB
    try:
        client, config = await db_client.get_session(handshake.client_key)
    except httpx.HTTPStatusError as e:
        logger.warning(f"[{handshake.client_key}] Handshake rechazado por jota-db ({e.response.status_code})")
        await websocket.close(code=1008, reason="Clave de cliente invalida o inactiva.")
        return
    except httpx.RequestError as e:
        logger.error(f"[{handshake.client_key}] jota-db no disponible durante handshake: {e}")
        await websocket.close(code=1011, reason="Servicio de identidad no disponible.")
        return

    logger.info(f"[{client.id}] Handshake verificado: key={handshake.client_key!r}")

    # 3. INSTANCIAR EL PUENTE DE MICROSERVICIOS
    try:
        orchestrator = websocket.scope["app"].state.orchestrators.default()
    except KeyError as e:
        logger.error(f"[{client.id}] No hay orquestador disponible: {e}")
        await websocket.close(code=1011, reason="No orchestrator available.")
        return
    bridge = JotaBridge(client=client, config=config, client_ws=websocket, orchestrator=orchestrator)
    bridge.handshake = handshake

    try:
        await bridge.connect_internal_services()
    except Exception as e:
        logger.error(f"[{client.id}] Fallo al inicializar puentes internos. {e}")
        await websocket.close(code=1011, reason="Problema estableciendo microservicios internos del hub.")
        return

    # 3.5 HEALTH CHECK — verifica disponibilidad de servicios antes de abrir la sesión
    if not await bridge.health_check():
        logger.warning(f"[{client.id}] Health check falló. Cerrando sesión.")
        await websocket.close(code=1011, reason="Servicio crítico no disponible.")
        return

    # 4. LANZAR LOOPS CONCURRENTES
    try:
        await bridge.run()
    except Exception as e:
        logger.error(f"[{client.id}] Error crítico de Runtime en el Puente Principal: {e}")
    finally:
        if "DISCONNECTED" not in websocket.client_state.name:
            try:
                await websocket.close()
            except Exception:
                pass

        logger.info(f"[{client.id}] --- Sesión de entrada WebSocket Concluida ---")
