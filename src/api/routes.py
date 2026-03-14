from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import logging
import json

from src.models.schemas import Handshake
from src.services.bridge import JotaBridge

logger = logging.getLogger(__name__)

router = APIRouter()

@router.websocket("/ws/stream/{client_id}")
async def gateway_websocket(websocket: WebSocket, client_id: str):
    await websocket.accept()
    
    # 1. FASE DE HANDSHAKE INITIAL
    try:
        # El primer mensaje esperado siempre es la config de modos JSON.
        raw_msg = await websocket.receive_text()
        msg_data = json.loads(raw_msg)
        handshake = Handshake(**msg_data)
        logger.info(f"[{client_id}] Handshake completado exitosamente: {handshake}")
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"[{client_id}] Payload inicial inválido de handshake. {e}")
        await websocket.close(code=1008, reason="Handshake invalido. Se esperaba JSON de configuración.")
        return
    except WebSocketDisconnect:
        logger.info(f"[{client_id}] Se desconecto inmediatamente antes o durante el handshake.")
        return

    # 2. INSTANCIAR EL PUENTE DE MICROSERVICIOS
    bridge = JotaBridge(client_id=client_id, client_ws=websocket)
    bridge.handshake = handshake
    
    try:
         # Tira las conexiones concurrentes a Orchestrator, Transcriber, TTS
         await bridge.connect_internal_services()
    except Exception as e:
         logger.error(f"[{client_id}] Fallo al inicializar puentes internos red docker. {e}")
         await websocket.close(code=1011, reason="Problema estableciendo microservicios internos del hub.")
         return

    # 3. LANZAR LOOPS CONCURRENTES
    try:
        # Este thread se queda en el método run() asíncronamente mientras los bucles corren.
        await bridge.run()
    except Exception as e:
        logger.error(f"[{client_id}] Error crítico de Runtime en el Puente Principal: {e}")
    finally:
        # Limpieza. bridge.run() o sus bucles detectaron quiebre / desconexión.
        # Desconecta cliente si aún esta activo.
        if "DISCONNECTED" not in websocket.client_state.name:
            # client_state se mapea de starlette, asumiendo lo cerramos si podemos
           try:
               await websocket.close()
           except Exception:
               pass
               
        logger.info(f"[{client_id}] --- Sesión de entrada WebSocket Concluida --- ")
