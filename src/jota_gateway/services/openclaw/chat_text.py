"""Reconstrucción del texto de una respuesta a partir de eventos `chat` (issues #241, #243).

OpenClaw 2026.10.5 deja de enviar `deltaText` en el `chat` `state:"final"`: el texto
completo viaja solo en `message`. Los tokens ya entregados no se pueden retractar, así
que en cada actualización se emite únicamente la parte que falta.
"""

import logging

logger = logging.getLogger(__name__)


def extract_message_text(message: object) -> str:
    """Texto de `message` (`{"role","content":[{"type":"text","text":...}]}`).

    Tolera `content` como string y descarta las partes que no son `text`. Cualquier
    forma inesperada devuelve "" en vez de lanzar.
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if isinstance(part, str):
            continue
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


_RETRACTED_MSG = "text retracted to a prefix of what was already emitted; nothing to emit"
_DIVERGED_MSG = "final text diverges from streamed text; emitting the full final"


def _is_retraction(emitted: str, text: str) -> bool:
    """`text` es un prefijo estricto y no vacio de lo ya emitido."""
    return bool(text) and len(text) < len(emitted) and emitted.startswith(text)


def _tail(emitted: str, text: str) -> tuple[str, str] | None:
    """(tipo, cola) si `text` encaja con `emitted`; None si diverge. Sin logs.

    tipo: "ok" (sufijo, tambien tras lstrip) o "retract" (prefijo estricto, cola "").
    """
    if text.startswith(emitted):
        return "ok", text[len(emitted):]
    e, f = emitted.lstrip(), text.lstrip()
    if e.strip() and f.startswith(e):
        return "ok", f[len(e):]
    if _is_retraction(emitted, text):
        return "retract", ""
    return None


def missing_tail(emitted: str, final_text: str) -> str:
    """Parte de `final_text` que aún no se emitió.

    1. `final_text` empieza por lo emitido -> el sufijo.
    2. Igual tras quitar espacios iniciales de ambos -> el sufijo del texto sin ellos.
    3. `final_text` es un prefijo estricto y no vacio de lo emitido (retractacion) ->
       nada (lo enviado no se retira) y un log fijo sin contenido.
    4. Lo emitido es solo espacios -> el texto completo, sin WARNING.
    5. Diverge de verdad -> el texto final completo (perder la respuesta es peor que
       repetir un fragmento) y un WARNING fijo, sin contenido.
    """
    hit = _tail(emitted, final_text)
    if hit is not None:
        if hit[0] == "retract":
            logger.info(_RETRACTED_MSG)
        return hit[1]
    if final_text and emitted.strip():
        logger.warning(_DIVERGED_MSG)
    return final_text


class ChatTextTracker:
    """Aplica la semantica oficial de OpenClaw 2026.10.5 a los eventos `chat` (#243).

    Mantiene `current` (el texto del mensaje segun OpenClaw), `emitted` (lo entregado,
    alineado con `current` tras cada emision) y `all_out` (concatenacion de TODOS los
    tokens entregados en la respuesta). Entrega solo lo que falta, comparando primero
    con `emitted` y, si diverge, con `all_out` (p. ej. tras un reemplazo vacio seguido
    de un `final` con el texto completo).

    - `deltaText` = lo anadido; con `replace:true` es el texto ENTERO de reemplazo.
    - `message` (snapshot) es autoritativo y ya incluye `deltaText`: no se suma dos veces.
    - `final` -> `current` = texto del `message` final.
    - Un `current` vacio (reemplazo/snapshot vacio) no emite ni retracta lo emitido.
    - `aborted`/`error` no emiten cola de texto (decision deliberada).
    """

    def __init__(self) -> None:
        self.emitted = ""
        self.current = ""
        self.all_out = ""

    def reset(self) -> None:
        self.emitted = ""
        self.current = ""
        self.all_out = ""

    def _emit_missing(self) -> str:
        if not self.current:
            return ""
        hit = _tail(self.emitted, self.current) or _tail(self.all_out, self.current)
        if hit is None:
            if self.all_out.strip():
                logger.warning(_DIVERGED_MSG)
            out = self.current
        elif hit[0] == "retract":
            # Lo enviado no se puede retirar: se conserva `emitted` para no duplicar
            # si el texto vuelve a crecer.
            logger.info(_RETRACTED_MSG)
            return ""
        else:
            out = hit[1]
        self.emitted = self.current
        self.all_out += out
        return out

    def feed(self, payload: dict) -> str:
        """Texto a emitir como token para este evento `chat` ("" si nada)."""
        state = payload.get("state")
        delta = payload.get("deltaText")
        if not isinstance(delta, str):
            delta = None
        message = payload.get("message")
        has_snapshot = isinstance(message, dict)
        snapshot = extract_message_text(message) if has_snapshot else ""

        if state == "final":
            if has_snapshot and (snapshot or delta is None):
                self.current = snapshot
            elif delta:
                self.current += delta
            out = self._emit_missing()
            self.reset()
            return out

        if state not in (None, "delta"):
            # Otros estados (status, aborted, error...) solo conservan el comportamiento
            # legacy de deltaText: nunca emiten cola a partir de `message`.
            if not delta:
                return ""
            self.current += delta
            return self._emit_missing()

        if has_snapshot:
            self.current = snapshot  # autoritativo: ya incluye deltaText
        elif delta is not None and payload.get("replace") is True:
            self.current = delta
        elif delta:
            self.current += delta
        else:
            return ""
        return self._emit_missing()
