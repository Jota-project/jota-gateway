from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import logging
import json

from src.models.schemas import Handshake
from src.services.bridge import JotaBridge

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
        client_id = handshake.client_key  # label de logs hasta que Fase 1 resuelva el UUID real
        logger.info(f"[{client_id}] Handshake completado exitosamente: {handshake}")
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"Payload inicial inválido de handshake. {e}")
        await websocket.close(code=1008, reason="Handshake invalido. Se esperaba JSON de configuración.")
        return
    except WebSocketDisconnect:
        logger.info(f"Cliente desconectado antes o durante el handshake.")
        return

    # 2. INSTANCIAR EL PUENTE DE MICROSERVICIOS
    bridge = JotaBridge(client_id=client_id, client_ws=websocket)
    bridge.handshake = handshake

    try:
        await bridge.connect_internal_services()
    except Exception as e:
        logger.error(f"[{client_id}] Fallo al inicializar puentes internos. {e}")
        await websocket.close(code=1011, reason="Problema estableciendo microservicios internos del hub.")
        return

    # 2.5 HEALTH CHECK — verifica disponibilidad de servicios antes de abrir la sesión
    if not await bridge.health_check():
        logger.warning(f"[{client_id}] Health check falló. Cerrando sesión.")
        await websocket.close(code=1011, reason="Servicio crítico no disponible.")
        return

    # 3. LANZAR LOOPS CONCURRENTES
    try:
        await bridge.run()
    except Exception as e:
        logger.error(f"[{client_id}] Error crítico de Runtime en el Puente Principal: {e}")
    finally:
        if "DISCONNECTED" not in websocket.client_state.name:
            try:
                await websocket.close()
            except Exception:
                pass

        logger.info(f"[{client_id}] --- Sesión de entrada WebSocket Concluida ---")
