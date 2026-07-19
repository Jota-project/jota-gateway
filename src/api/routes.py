from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import logging
import json
import time

from src.core.agent_policy import AgentPolicyError, resolve_agent
from src.core.exceptions import ClientInactive, ClientNotFound
from src.core.logging import fingerprint_key
from src.core.network import resolve_client_ip
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
    request_id = websocket.state.request_id
    key_fingerprint = fingerprint_key(handshake.client_key)
    try:
        client, config = await db_client.get_session(handshake.client_key)
    except (ClientNotFound, ClientInactive):
        logger.warning(
            f"request_id={request_id} source_ip={resolve_client_ip(websocket)} "
            f"fp={key_fingerprint} Handshake rechazado: key inválida o cliente inactivo."
        )
        await websocket.close(code=1008, reason="Clave de cliente invalida o inactiva.")
        return

    logger.info(
        f"request_id={request_id} client_id={client.id} fp={key_fingerprint} "
        "Handshake verificado."
    )

    # 3. INSTANCIAR EL PUENTE DE MICROSERVICIOS
    app_state = websocket.scope["app"].state
    openclaw = app_state.openclaw

    # Per-client agent policy enforcement (issue #105).
    # resolve_agent owns the cascade (requested -> client_config.default_agent
    # -> gateway_info.default_agent_id -> "main") and the policy checks
    # (allowlist before global roster).
    try:
        resolved_agent = resolve_agent(
            requested=handshake.agent,
            client_config=config,
            gateway_info=openclaw.gateway_info,
        )
    except AgentPolicyError as e:
        logger.warning(f"[{client.id}] Agent policy rejected: {e}")
        await websocket.close(code=1008, reason=str(e))
        return

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
        tts=app_state.tts,
        tracker=tracker, handshake=handshake,
        client_registry=app_state.client_registry,
        default_agent=resolved_agent,
    )

    # One single try/finally covers setup (connect_internal_services, health
    # check, ready send) and bridge.run(): any early return/exception below
    # still reaches bridge.close_all() — a failed handshake during an
    # OpenClaw outage used to leak the transcriber socket, the bridge
    # registration, and leave the session "active" forever (issue #101).
    # bridge.run() already calls close_all() itself in its own finally on
    # the normal-completion path, so this always runs it a second time —
    # close_all() is idempotent, the first call's status wins.
    try:
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
        # resolved_agent comes from the policy helper above.
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
        await bridge.close_all(status="error")
        if "DISCONNECTED" not in websocket.client_state.name:
            try:
                await websocket.close()
            except Exception:
                pass

        logger.info(f"[{client.id}] --- Sesión de entrada WebSocket Concluida ---")
