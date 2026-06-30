from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import httpx
import logging
import json
import time

from src.models.schemas import Handshake
from src.services.bridge import JotaBridge
from src.services.db_client import db_client
from src.services.pipeline_tracker import PipelineTracker

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
    app_state = websocket.scope["app"].state
    openclaw = app_state.openclaw

    # Validate requested agent against hello-ok agent list
    requested_agent = handshake.agent
    if requested_agent and openclaw.gateway_info and not openclaw.gateway_info.has_agent(requested_agent):
        logger.warning(f"[{client.id}] Requested agent '{requested_agent}' not in OpenClaw")
        await websocket.close(code=1008, reason=f"Agent '{requested_agent}' not available.")
        return

    default_agent = openclaw.gateway_info.default_agent_id if openclaw.gateway_info else "main"

    session_id = f"{client.id}:{int(time.time() * 1000)}"
    session_registry = app_state.session_registry
    tracker = PipelineTracker(
        session_id=session_id,
        client_id=client.id,
        input_mode=handshake.input_mode,
        output_mode=handshake.output_mode,
        client_ws=websocket,
        registry=session_registry,
    )
    session_registry.register(tracker)
    bridge = JotaBridge(
        client=client, config=config, client_ws=websocket, orchestrator=openclaw,
        tracker=tracker, handshake=handshake,
        client_registry=app_state.client_registry,
        default_agent=default_agent,
    )

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

    # 3.6 SEND READY — confirms session is established and announces capabilities
    resolved_agent = handshake.agent or default_agent
    try:
        await websocket.send_json({
            "type": "ready",
            "session_id": session_id,
            "agent": resolved_agent,
            "input_mode": handshake.input_mode,
            "output_mode": handshake.output_mode,
            "capabilities": {
                "barge_in": config.barge_in_enabled,
                "tts": "audio" in handshake.output_mode,
                "transcriber": handshake.input_mode == "audio",
            },
        })
    except Exception as e:
        logger.warning(f"[{client.id}] Failed to send ready: {e}")
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
