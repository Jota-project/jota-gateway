import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from fogar_gateway.api.admin_routes import panel_session
from fogar_gateway.api.deps import admin_token_matches
from fogar_gateway.core.agent_policy import AgentPolicyError, resolve_agent
from fogar_gateway.core.config import settings
from fogar_gateway.core.exceptions import ClientInactive, ClientNotFound
from fogar_gateway.core.logging import fingerprint_key
from fogar_gateway.core.network import resolve_client_ip
from fogar_gateway.core.transport import drop_slow_client
from fogar_gateway.models.schemas import Client, ClientConfig, Handshake
from fogar_gateway.services.bridge import FogarBridge
from fogar_gateway.services.conversations import (
    NEW,
    ConversationClientMissing,
    ConversationNotFound,
    ConversationService,
    SessionConversation,
)
from fogar_gateway.services.db_client import db_client
from fogar_gateway.services.openclaw.models import GatewayInfo
from fogar_gateway.services.outbound import ClientGone, QueuedSender
from fogar_gateway.services.pipeline_tracker import PipelineTracker
from fogar_gateway.services.reconnection import describe_error, traceback_no_message
from fogar_gateway.services.stats import gateway_stats
from fogar_gateway.services.teardown import Teardown

logger = logging.getLogger(__name__)

router = APIRouter()

# Motivos (reason) estables de los cierres 1008 de conversación (docs/client-protocol.md §1).
CONVERSATION_NOT_FOUND = "conversation not found"
CONVERSATION_AGENT_MISMATCH = "conversation agent mismatch"


class _Rejected(Exception):
    """Un handshake rechazado con 1008: `reason` va al cliente; `kind`, al log."""

    def __init__(self, kind: str, reason: str, stat: str) -> None:
        super().__init__(kind)
        self.kind, self.reason, self.stat = kind, reason, stat


async def _authenticate(handshake: Handshake) -> tuple[Client, ClientConfig, str]:
    """`(Client, ClientConfig, quién para el log)` de la credencial del handshake.

    `admin_token` (M19): el panel web, que entra como el cliente «Panel web» si coincide con
    ADMIN_TOKEN (`deps.admin_token_matches`, la misma comprobación que `/admin`). Lanza
    ClientNotFound/ClientInactive.
    """
    if handshake.admin_token is not None:
        if not admin_token_matches(handshake.admin_token):
            raise ClientNotFound("panel")
        client, config = await panel_session()
        return client, config, "panel"
    key = handshake.client_key or ""  # Handshake exige una de las dos credenciales
    client, config = await db_client.get_session(key)
    return client, config, f"fp={fingerprint_key(key)}"


async def _open_conversation(
    service: ConversationService,
    client_id: str,
    config: ClientConfig,
    handshake: Handshake,
    gateway_info: GatewayInfo | None,
) -> SessionConversation:
    """La conversación de la sesión: la pedida (`conversation`, solo del propio cliente), una
    nueva (`"new"`) o, sin pedir, la del día. Lanza `_Rejected` (1008)."""
    requested = handshake.conversation
    existing = None
    if requested is not None and requested != NEW:
        try:
            existing = await service.get_owned(client_id, requested)
        except ConversationNotFound:
            raise _Rejected("conversation_not_found", CONVERSATION_NOT_FOUND, "conversation") from None
        asked = (handshake.agent or "").strip()
        if asked and asked != existing.agent:
            raise _Rejected("conversation_agent", CONVERSATION_AGENT_MISMATCH, "conversation")
    try:
        # Una conversación existente fija su agente: se le aplican la lista y el roster.
        agent = resolve_agent(
            requested=existing.agent if existing is not None else handshake.agent,
            client_config=config,
            gateway_info=gateway_info,
        )
    except AgentPolicyError as e:
        raise _Rejected(f"agent {e.kind}", str(e), "agent") from None
    if existing is not None:
        conversation = existing
    elif requested == NEW:
        conversation = await service.create(client_id, agent)
    else:
        conversation = await service.default_for(client_id, agent)
    return SessionConversation(conversation, explicit=requested is not None, service=service)


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

    # 2. RESOLVER IDENTIDAD EN LA BD LOCAL
    request_id = websocket.state.request_id
    try:
        client, config, who = await _authenticate(handshake)
    except (ClientNotFound, ClientInactive):
        # El admin_token nunca va al log, ni su huella; la client_key solo como huella.
        who = "panel" if handshake.client_key is None else f"fp={fingerprint_key(handshake.client_key)}"
        logger.warning(
            f"request_id={request_id} source_ip={resolve_client_ip(websocket)} "
            f"{who} Handshake rechazado: credencial inválida o cliente inactivo."
        )
        gateway_stats.handshake_rejected("invalid_key")
        await websocket.close(code=1008, reason="Clave de cliente invalida o inactiva.")
        return
    # La credencial ya no hace falta: la sesión guarda el handshake sin el admin_token.
    handshake = handshake.model_copy(update={"admin_token": None})

    logger.info(f"request_id={request_id} client_id={client.id} {who} Handshake verificado.")

    # 3. INSTANCIAR EL PUENTE DE MICROSERVICIOS
    app_state = websocket.scope["app"].state
    openclaw = app_state.openclaw

    # Agente (#105, #245) y conversación (M19): la cascada y la política de resolve_agent, y la
    # conversación pedida (del propio cliente) o la del día.
    try:
        conversation = await _open_conversation(
            app_state.conversations, client.id, config, handshake, openclaw.gateway_info
        )
    except _Rejected as e:
        # Solo el tipo de rechazo; el reason del 1008 sí lleva el texto (protocolo, §1).
        logger.warning(f"[{client.id}] Handshake rechazado: {e.kind}")
        gateway_stats.handshake_rejected(e.stat)
        await websocket.close(code=1008, reason=e.reason)
        return
    except ConversationClientMissing:
        # Borrado entre la autenticación y el alta de la conversación: su clave ya no vale.
        logger.warning(f"[{client.id}] Handshake rechazado: el cliente ya no existe.")
        gateway_stats.handshake_rejected("invalid_key")
        await websocket.close(code=1008, reason="Clave de cliente invalida o inactiva.")
        return
    except Exception as e:  # noqa: BLE001 — frontera: sin conversación no hay sesión (1011)
        logger.error(f"[{client.id}] Fallo al abrir la conversación: {describe_error(e)}")
        logger.debug(f"[{client.id}] conversación: {traceback_no_message(e)}")
        await websocket.close(
            code=1011, reason="Problema estableciendo microservicios internos del hub."
        )
        return
    resolved_agent = conversation.current.agent

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
    # G-24 (#221): the writer task is already running; if building the rest fails, close
    # it and the session instead of leaving them orphaned. Construction is synchronous,
    # so only an Exception can land here.
    try:
        tracker = PipelineTracker(
            session_id=session_id,
            client_id=client.id,
            input_mode=handshake.input_mode,
            output_mode=handshake.output_mode,
            registry=session_registry,
            sender=sender,
        )
        session_registry.register(tracker)
        bridge = FogarBridge(
            client=client,
            config=config,
            client_ws=websocket,
            orchestrator=openclaw,
            speech=app_state.speech,
            tracker=tracker,
            handshake=handshake,
            client_registry=app_state.client_registry,
            conversation=conversation,
            sender=sender,
            turn_registry=app_state.turn_registry,
        )
    except Exception as e:  # noqa: BLE001 — frontera: sesión a medio construir
        logger.error(f"[{client.id}] Fallo al construir la sesión: {describe_error(e)}")
        logger.debug(f"[{client.id}] construcción de la sesión: {traceback_no_message(e)}")
        async with Teardown(f"[{client.id}] sesión a medio construir") as td:
            session_registry.close(session_id, "error")  # no-op si no llegó a registrarse
            await td.step(sender.aclose(settings.SESSION_TEARDOWN_S), "cierre de la salida")
            await td.step(
                websocket.close(
                    code=1011, reason="Problema estableciendo microservicios internos del hub."
                ),
                "cierre del WebSocket",
                settings.SESSION_TEARDOWN_S,
            )
        return

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
        except Exception as e:  # noqa: BLE001 — frontera: el arranque fallido cierra con 1011
            logger.error(
                f"[{client.id}] Fallo al inicializar puentes internos: {describe_error(e)}"
            )
            logger.debug(f"[{client.id}] puentes internos: {traceback_no_message(e)}")
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

        # 3.55 CONVERSACIÓN (M19): solo una sesión que pasó el health check la abre; la que la
        # tuviera se cierra (1008) y se espera a que suelte sus turnos antes de `ready`.
        await bridge.claim_conversation()

        # 3.6 SEND READY — confirms session is established and announces capabilities
        # resolved_agent comes from the policy helper above.
        try:
            await sender.send_json(
                {
                    "type": "ready",
                    "session_id": session_id,
                    "agent": resolved_agent,
                    "conversation": {"id": conversation.id, "agent": resolved_agent},
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
        except Exception as e:  # noqa: BLE001 — frontera: sin `ready` no hay sesión
            logger.warning(f"[{client.id}] Failed to send ready: {describe_error(e)}")
            bridge.note_close_reason("client_disconnect" if isinstance(e, ClientGone) else "error")
            return

        # 4. LANZAR LOOPS CONCURRENTES
        try:
            await bridge.run()
        except Exception as e:  # noqa: BLE001 — frontera: el finally cierra la sesión
            logger.error(
                f"[{client.id}] Error crítico de Runtime en el Puente Principal: "
                f"{describe_error(e)}"
            )
            logger.debug(f"[{client.id}] puente principal: {traceback_no_message(e)}")
    finally:
        # Each step runs even if this handler is cancelled; the cancellation is
        # re-raised at the end (#221). close_all() bounds each of its own steps.
        async with Teardown(f"[{client.id}] fin de sesión") as td:
            await td.step(bridge.close_all(status="error"), "close_all")
            # close_all() queued session_end; drain it before the socket goes away.
            await td.step(sender.aclose(settings.SESSION_TEARDOWN_S), "drenado de la salida")
            if "DISCONNECTED" not in websocket.client_state.name:
                await td.step(
                    websocket.close(), "cierre del WebSocket", settings.SESSION_TEARDOWN_S
                )
            logger.info(f"[{client.id}] --- Sesión de entrada WebSocket Concluida ---")
