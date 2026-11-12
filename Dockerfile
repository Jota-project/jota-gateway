# Python minor + digest fijados (el digest lo mantiene Dependabot, ecosistema "docker").
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

WORKDIR /app

# uv.lock es la única fuente de versiones. uv solo exporta el lock a un requirements con hashes
# (sin el proyecto ni el extra "dev": nada de pytest/ruff/mypy en la imagen) y pip lo instala
# con --require-hashes. Se hace antes de copiar el código para aprovechar la caché de capas.
# La versión y el digest de uv los mantiene Dependabot (ecosistema "docker").
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/req.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/req.txt \
    && rm /tmp/req.txt

# El paquete, igual que en la instalación nativa: trae Alembic y sus migraciones y el ejecutable
# fogar-gw. Se construye en /tmp para que /app solo tenga los datos. README.md lo pide pyproject.
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
# Directorio base del gateway y de fogar-gw: la BD relativa de DATABASE_URL queda en /app/data,
# la del volumen. Aquí no hay config.env: la configuración llega como variables (env_file).
ENV FOGAR_GATEWAY_HOME=/app

EXPOSE 8004

# /healthz es liveness pura (siempre 200 si el proceso responde). La imagen slim no trae curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8004/healthz', timeout=3)"]

# Host por GATEWAY_HOST (default 0.0.0.0). Detrás de un reverse proxy en el mismo host conviene
# GATEWAY_HOST=127.0.0.1 (el HEALTHCHECK de arriba usa 127.0.0.1, así que no sirve con una IP de
# LAN concreta). Los demás ajustes de uvicorn están en src/fogar_gateway/cli/serve.py.
# --create-db: con un ./data vacío, `docker compose up` crea la BD como siempre (decisión 5 de M14c).
CMD ["fogar-gw", "serve", "--create-db"]
