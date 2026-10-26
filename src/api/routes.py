import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from src.core.agent_policy import AgentPolicyError, resolve_agent
from src.core.config import settings
from src.core.exceptions import ClientInactive, ClientNotFound
from src.core.logging import fingerprint_key
from src.core.network import resolve_client_ip
from src.core.transport import drop_slow_client
from src.models.schemas import Handshake
from src.services.bridge import JotaBridge
from src.services.db_client import db_client
from src.services.outbound import ClientGone, QueuedSender
from src.services.pipeline_tracker import PipelineTracker
from src.services.reconnection import describe_error
from src.services.stats import gateway_stats

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/stream")
async def gateway_websocket(websocket: WebSocket):
    await websocket.accept()

    # 1. FASE DE HANDSHAKE INITIAL
    try:
        raw_msg = await asyncio.wait_for(
            websocket.receive_text(), timeout=settings.HANDSHAKE_TIMEOUT_S
        )
        if len(raw_msg) > settings.HANDSHAKE_MAX_BYTES:
            # Antes de parsear/autenticar: json.loads + pydantic cuestan ~8x el tamaño.
            logger.warning(
                f"Handshake rechazado: {len(raw_msg)} caracteres > HANDSHAKE_MAX_BYTES."
            )
            gateway_stats.handshake_rejected("too_large")
            await websocket.close(code=1008, reason="Handshake too large")
            return
        msg_data = json.loads(raw_msg)
        handshake = Handshake(**msg_data)
    except (json.JSONDecodeError, ValidationError) as e:
        # G-27b (#216): str(ValidationError) incluye `input_value=` con el dict entero
        # (la client_key). Solo campo y tipo de error; de JSONDecodeError, su mensaje
        # (posición, sin el documento).
        if isinstance(e, ValidationError):
            detail = ", ".join(
                f"{'.'.join(str(p) for p in err['loc'])}:{err['type']}"
                for err in e.errors(include_input=False, include_url=False, include_context=False)
            )
        else:
            detail = f"JSON inválido ({e.msg})"
        logger.error(f"Payload inicial inválido de handshake. {detail}")
        gateway_stats.handshake_rejected("invalid")
        await websocket.close(
            code=1008, reason="Handshake invalido. Se esperaba JSON de configuración."
        )
        return
    except TimeoutError:
        logger.warning("Handshake no recibido dentro de HANDSHAKE_TIMEOUT_S.")
        gateway_stats.handshake_rejected("timeout")
        await websocket.close(code=1008, reason="Handshake timeout")
        return
    except KeyError:
        # #232: starlette's receive_text() lee message["text"]; un primer frame binario
        # (o cualquier mensaje sin "text") lanza KeyError. Sin contenido en el log.
        logger.warning("Handshake rechazado: el primer mensaje no es texto.")
        gateway_stats.handshake_rejected("not_text")
        await websocket.close(code=1008, reason="Handshake invalido. Se esperaba texto.")
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
        gateway_stats.handshake_rejected("invalid_key")
        await websocket.close(code=1008, reason="Clave de cliente invalida o inactiva.")
        return

    logger.info(
        f"request_id={request_id} client_id={client.id} fp={key_fingerprint} Handshake verificado."
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
        # Solo el tipo de rechazo; el reason del 1008 sí lleva el texto (protocolo, §1).
        logger.warning(f"[{client.id}] Agent policy rejected: {e.kind}")
        gateway_stats.handshake_rejected("agent")
        await websocket.close(code=1008, reason=str(e))
        return

    session_id = f"{client.id}:{int(time.time() * 1000)}"
    session_registry = app_state.session_registry
    # One outbound path per session: sender -> tracker -> bridge (issue #130 S1); a client
    # that stops reading is dropped (issue #130 S2).
    # Created only after every handshake rejection above, so no writer task leaks.
    async def _on_slow_client() -> None:
        # `tracker` is created right below; this only runs when the writer's send times
        # out, long after the session is fully constructed.
        bridge.note_close_reason("slow_client")
        await drop_slow_client(
            websocket,
            record=lambda: tracker.record("client_slow", timeout_s=settings.CLIENT_SEND_TIMEOUT_S),
        )

    sender = QueuedSender(websocket, on_slow=_on_slow_client)
    tracker = PipelineTracker(
        session_id=session_id,
        client_id=client.id,
        input_mode=handshake.input_mode,
        output_mode=handshake.output_mode,
        client_ws=websocket,
        registry=session_registry,
        sender=sender,
    )
    session_registry.register(tracker)
    bridge = JotaBridge(
        client=client,
        config=config,
        client_ws=websocket,
        orchestrator=openclaw,
        tts=app_state.speech.tts,
        stt=app_state.speech.stt,
        speech=app_state.speech,
        tracker=tracker,
        handshake=handshake,
        client_registry=app_state.client_registry,
        default_agent=resolved_agent,
        sender=sender,
        turn_registry=getattr(app_state, "turn_registry", None),
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
            logger.error(
                f"[{client.id}] Fallo al inicializar puentes internos: {describe_error(e)}"
            )
            await websocket.close(
                code=1011, reason="Problema estableciendo microservicios internos del hub."
            )
            return

        # 3.5 HEALTH CHECK — verifica disponibilidad de servicios antes de abrir la sesión
        try:
            live_capabilities = await bridge.health_check()
        except ClientGone:
            # #205: el cliente se fue durante el arranque; es una desconexión normal.
            # El `finally` de abajo hace close_all / sender.aclose.
            logger.info(f"[{client.id}] Cliente desconectado durante el health check.")
            bridge.note_close_reason("client_disconnect")
            return
        if live_capabilities is None:
            logger.warning(f"[{client.id}] Health check falló. Cerrando sesión.")
            bridge.note_close_reason("error")
            try:
                await sender.flush()  # deliver the queued `status` before the 1011 close
            except ClientGone:
                pass
            await websocket.close(code=1011, reason="Servicio crítico no disponible.")
            return

        # 3.6 SEND READY — confirms session is established and announces capabilities
        # resolved_agent comes from the policy helper above.
        try:
            await sender.send_json(
                {
                    "type": "ready",
                    "session_id": session_id,
                    "agent": resolved_agent,
                    "input_mode": handshake.input_mode,
                    "output_mode": handshake.output_mode,
                    "requested_capabilities": {
                        "barge_in": config.barge_in_enabled,
                        "tts": "audio" in handshake.output_mode,
                        "transcriber": handshake.input_mode == "audio",
                    },
                    "live_capabilities": live_capabilities,
                }
            )
            await sender.flush()
        except Exception as e:
            logger.warning(f"[{client.id}] Failed to send ready: {describe_error(e)}")
            bridge.note_close_reason("client_disconnect" if isinstance(e, ClientGone) else "error")
            return

        # 4. LANZAR LOOPS CONCURRENTES
        try:
            await bridge.run()
        except Exception as e:
            logger.error(
                f"[{client.id}] Error crítico de Runtime en el Puente Principal: "
                f"{describe_error(e)}"
            )
    finally:
        await bridge.close_all(status="error")
        # close_all() queued session_end; drain it before the socket goes away.
        try:
            await sender.aclose(settings.SHUTDOWN_DRAIN_S)
        except Exception as e:  # aclose never raises by contract; be defensive
            logger.debug(f"[{client.id}] sender.aclose falló: {e}")
        if "DISCONNECTED" not in websocket.client_state.name:
            try:
                await websocket.close()
            except Exception:
                pass

        logger.info(f"[{client.id}] --- Sesión de entrada WebSocket Concluida ---")
