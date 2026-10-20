"""Reconstrucción del texto de una respuesta a partir de eventos `chat` (issue #241).

OpenClaw 2026.10.5 deja de enviar `deltaText` en el `chat` `state:"final"`: el texto
completo viaja solo en `message`. Los tokens ya entregados no se pueden retractar, así
que en `final` se emite únicamente la parte que falta.
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


def _is_retraction(emitted: str, text: str) -> bool:
    """`text` es un prefijo estricto y no vacio de lo ya emitido."""
    return bool(text) and len(text) < len(emitted) and emitted.startswith(text)


def missing_tail(emitted: str, final_text: str) -> str:
    """Parte de `final_text` que aún no se emitió.

    1. `final_text` empieza por lo emitido -> el sufijo.
    2. Igual tras quitar espacios iniciales de ambos -> el sufijo del texto sin ellos.
    3. `final_text` es un prefijo estricto y no vacio de lo emitido (retractacion) ->
       nada (lo enviado no se retira) y un log fijo sin contenido.
    4. Diverge de verdad -> el texto final completo (perder la respuesta es peor que
       repetir un fragmento) y un WARNING fijo, sin contenido.
    """
    if final_text.startswith(emitted):
        return final_text[len(emitted):]
    e, f = emitted.lstrip(), final_text.lstrip()
    if e.strip() and f.startswith(e):
        return f[len(e):]
    if _is_retraction(emitted, final_text):
        logger.info(_RETRACTED_MSG)
        return ""
    if final_text:
        logger.warning("final text diverges from streamed text; emitting the full final")
    return final_text


class ChatTextTracker:
    """Aplica la semantica oficial de OpenClaw 2026.10.5 a los eventos `chat` (#243).

    Mantiene `current` (el texto del mensaje segun OpenClaw) y `emitted` (lo ya
    entregado, alineado con `current` tras cada emision) y entrega solo lo que falta.

    - `deltaText` = lo anadido; con `replace:true` es el texto ENTERO de reemplazo.
    - `message` (snapshot) es autoritativo y ya incluye `deltaText`: no se suma dos veces.
    - `final` -> `current` = texto del `message` final.
    - Un `current` vacio (reemplazo/snapshot vacio) no emite ni retracta lo emitido.
    """

    def __init__(self) -> None:
        self.emitted = ""
        self.current = ""

    def reset(self) -> None:
        self.emitted = ""
        self.current = ""

    def _emit_missing(self) -> str:
        if not self.current:
            return ""
        if _is_retraction(self.emitted, self.current):
            # Lo enviado no se puede retirar: se conserva `emitted` para no duplicar
            # si el texto vuelve a crecer.
            logger.info(_RETRACTED_MSG)
            return ""
        out = missing_tail(self.emitted, self.current)
        self.emitted = self.current
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
            # Otros estados (status...) solo conservan el comportamiento legacy de deltaText.
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
