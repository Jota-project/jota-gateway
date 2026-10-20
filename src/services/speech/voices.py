"""Resolución de la voz configurada de un cliente a la voz del proveedor activo."""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Valores ya avisados: un WARNING por voz distinta, no uno por turno.
_warned: set[str] = set()


def resolve_voice(
    configured: str | None,
    *,
    voice_map: dict[str, str],
    accepts_native: Callable[[str], bool],
    default: str | None,
) -> str | None:
    """Mapa -> voz nativa del proveedor -> voz por defecto (con un aviso por valor)."""
    if configured and configured in voice_map:
        return voice_map[configured]
    if configured and accepts_native(configured):
        return configured
    if configured and configured not in _warned:
        _warned.add(configured)
        logger.warning(
            "Voz %r sin equivalente en el proveedor TTS: se usa la voz por defecto "
            "(añádela a TTS_VOICE_MAP)",
            configured,
        )
    return default
