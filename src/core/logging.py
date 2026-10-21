"""Safe logging helpers for secrets and credentials."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal


def fingerprint_key(value: str) -> str:
    """Return a non-reversible eight-character SHA-256 fingerprint."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


_STD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}
_TEXT_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JsonFormatter(logging.Formatter):
    """Una línea JSON por registro; los `extra` van como campos de primer nivel."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        data.update({k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS})
        return json.dumps(data, default=str, ensure_ascii=False)


class HealthzAccessFilter(logging.Filter):
    """Quita del log de acceso de uvicorn las sondas de liveness (`/healthz`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3 and args[2] == "/healthz")


def configure_logging(fmt: Literal["text", "json"]) -> None:
    formatter: logging.Formatter = (
        JsonFormatter() if fmt == "json" else logging.Formatter(_TEXT_FORMAT)
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    if fmt == "json":
        # uvicorn configura sus loggers antes de importar la app: reasigna su formatter.
        for name in _UVICORN_LOGGERS:
            for h in logging.getLogger(name).handlers:
                h.setFormatter(formatter)
    logging.getLogger("uvicorn.access").addFilter(HealthzAccessFilter())
