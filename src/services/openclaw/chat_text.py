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


def missing_tail(emitted: str, final_text: str) -> str:
    """Parte de `final_text` que aún no se emitió.

    1. `final_text` empieza por lo emitido -> el sufijo.
    2. Igual tras quitar espacios iniciales de ambos -> el sufijo del texto sin ellos.
    3. Diverge de verdad -> el texto final completo (perder la respuesta es peor que
       repetir un fragmento) y un WARNING fijo, sin contenido.
    """
    if final_text.startswith(emitted):
        return final_text[len(emitted):]
    e, f = emitted.lstrip(), final_text.lstrip()
    if e.strip() and f.startswith(e):
        return f[len(e):]
    if final_text:
        logger.warning("final text diverges from streamed text; emitting the full final")
    return final_text


class ChatTextTracker:
    """Acumula lo emitido y calcula el token a entregar por cada evento `chat`."""

    def __init__(self) -> None:
        self.emitted = ""

    def reset(self) -> None:
        self.emitted = ""

    def feed(self, payload: dict) -> str:
        """Texto a emitir como token para este evento `chat` ("" si nada)."""
        if payload.get("state") == "final":
            delta = payload.get("deltaText") or ""
            if not isinstance(delta, str):
                delta = ""
            self.emitted += delta
            final_text = extract_message_text(payload.get("message"))
            out = delta
            if final_text:
                out += missing_tail(self.emitted, final_text)
            self.reset()
            return out
        delta = payload.get("deltaText") or ""
        if not isinstance(delta, str) or not delta:
            return ""
        self.emitted += delta
        return delta
