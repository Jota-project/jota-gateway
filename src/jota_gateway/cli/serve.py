"""
serve.py
~~~~~~~~
`jota-gw serve`: arranca el gateway. Los ajustes de uvicorn viven aquí, no en el CMD de la
imagen: la instalación nativa y Docker arrancan igual.
"""

import os

import click

PORT = 8004
# Tope de cualquier mensaje WebSocket entrante (default de uvicorn: 16 MiB); 1 MiB sobra para
# audio PCM en chunks y para los mensajes de control.
WS_MAX_SIZE = 1_048_576
# Por debajo del stop_grace_period (45 s) de docker-compose y del TimeoutStopSec de la unidad.
GRACEFUL_SHUTDOWN_S = 35


@click.command()
@click.option("--create-db", is_flag=True,
              help="Crea la BD si no existe (la imagen Docker). Sin él, se para y lo explica.")
def serve(create_db: bool) -> None:
    """Arranca el gateway (puerto 8004, host GATEWAY_HOST)."""
    os.umask(0o077)  # lo que cree (BD, candado, data/) solo para su dueño
    import uvicorn
    from pydantic import ValidationError

    try:
        from jota_gateway.core.config import settings  # construye Settings(): aquí falla
    except ValidationError as e:
        from jota_gateway.cli.config import _errors

        # Solo campo y tipo (nunca input_value: podría ser una clave); exit 3 = reintentar
        # no lo arregla (RestartPreventExitStatus de la unidad).
        click.echo("Configuración no válida:\n" + "\n".join(f"  {m}" for m in _errors(e)),
                   err=True)
        raise SystemExit(3) from None
    from jota_gateway.db import database

    database.CREATE_DB = create_db
    uvicorn.run(
        # Como cadena: uvicorn configura sus loggers antes de importar la app, y
        # configure_logging (core/logging.py) cuenta con ese orden para el formato JSON.
        "jota_gateway.main:app",
        host=settings.GATEWAY_HOST or "0.0.0.0",
        port=PORT,
        # La única señal de «IP real detrás de un proxy» es X-Real-IP (core/network.py); con
        # proxy_headers, un X-Forwarded-For reenviado por el proxy reescribiría el peer TCP.
        proxy_headers=False,
        ws_max_size=WS_MAX_SIZE,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
    )
