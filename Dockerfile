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

# El paquete, igual que en la instalación nativa: trae Alembic y sus migraciones y el ejecutable
# jota-gw. Se construye en /tmp para que /app solo tenga los datos. README.md lo pide pyproject.
COPY pyproject.toml README.md /tmp/pkg/
COPY src/ /tmp/pkg/src/
RUN pip install --no-cache-dir --no-deps /tmp/pkg && rm -rf /tmp/pkg

# Usuario no-root. /app/data queda en su propiedad para que la imagen funcione
# también sin bind mount; con docker-compose el UID/GID real viene de
# GATEWAY_UID/GATEWAY_GID (ver docker-compose.yml) para coincidir con el dueño de ./data.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data
USER app

ENV LOG_FORMAT=json
# Directorio base del gateway y de jota-gw: la BD relativa de DATABASE_URL queda en /app/data,
# la del volumen. Aquí no hay config.env: la configuración llega como variables (env_file).
ENV JOTA_GATEWAY_HOME=/app

EXPOSE 8004

# /healthz es liveness pura (siempre 200 si el proceso responde). La imagen slim no trae curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8004/healthz', timeout=3)"]

# Host por GATEWAY_HOST (default 0.0.0.0). Detrás de un reverse proxy en el mismo host conviene
# GATEWAY_HOST=127.0.0.1 (el HEALTHCHECK de arriba usa 127.0.0.1, así que no sirve con una IP de
# LAN concreta). Los demás ajustes de uvicorn están en src/jota_gateway/cli/serve.py.
# --create-db: con un ./data vacío, `docker compose up` crea la BD como siempre (decisión 5 de M14c).
CMD ["jota-gw", "serve", "--create-db"]
