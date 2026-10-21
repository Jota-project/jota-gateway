# Python minor + digest fijados (el digest lo mantiene Dependabot, ecosistema "docker").
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

WORKDIR /app

# pyproject.toml es la única fuente de dependencias: extraemos solo
# [project].dependencies (sin el extra "dev": nada de pytest/ruff/mypy en la imagen)
# y las instalamos antes de copiar el código para aprovechar la caché de capas.
COPY pyproject.toml .
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copiamos el código, la config y las migraciones de Alembic (run_migrations() las necesita en runtime)
COPY src/ /app/src/
COPY alembic.ini .
COPY migrations/ /app/migrations/

# Usuario no-root. /app/data queda en su propiedad para que la imagen funcione
# también sin bind mount; con docker-compose el UID/GID real viene de
# GATEWAY_UID/GATEWAY_GID (ver docker-compose.yml) para coincidir con el dueño de ./data.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data
USER app

ENV LOG_FORMAT=json

EXPOSE 8004

# /healthz es liveness pura (siempre 200 si el proceso responde). La imagen slim no trae curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8004/healthz', timeout=3)"]

# Host de escucha por GATEWAY_HOST (default 0.0.0.0, como antes). Detrás de un reverse
# proxy en el mismo host conviene GATEWAY_HOST=127.0.0.1 (el HEALTHCHECK de arriba usa
# 127.0.0.1, así que no sirve con una IP de LAN concreta).
# --no-proxy-headers: la única señal de "IP real detrás de un proxy" es X-Real-IP (ver
# src/core/network.py); sin él uvicorn dejaría que un X-Forwarded-For reenviado por el
# proxy reescribiera el peer TCP.
# --ws-max-size: tope de cualquier mensaje WebSocket entrante (default de uvicorn: 16 MiB);
# 1 MiB sobra para audio PCM en chunks y para los mensajes de control.
CMD ["sh", "-c", "exec uvicorn src.main:app --host \"${GATEWAY_HOST:-0.0.0.0}\" --port 8004 --no-proxy-headers --ws-max-size 1048576 --timeout-graceful-shutdown 35"]
