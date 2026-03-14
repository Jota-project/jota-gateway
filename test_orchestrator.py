"""
Script de prueba rápida del OrchestratorClient.
Envía un prompt sencillo y muestra los eventos NDJSON que devuelve.
"""
import asyncio
import sys
import os

# Añadir src al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from services.orchestrator_client import OrchestratorClient
from core.config import settings


async def main():
    prompt = "Hola, ¿cómo estás?"

    print(f"Base URL:  {settings.ORCHESTRATOR_BASE_URL}")
    print(f"API Key:   {settings.ORCHESTRATOR_API_KEY}")
    print(f"Prompt:    {prompt!r}")
    print("-" * 50)

    client = OrchestratorClient(
        base_url=settings.ORCHESTRATOR_BASE_URL,
        api_key=settings.ORCHESTRATOR_API_KEY,
        client_id="test-gateway",
    )

    await client.connect()

    full_response = []
    try:
        async for event in client.stream_response(prompt):
            print(f"EVENT: {event}")
            if event.get("type") == "token":
                full_response.append(event.get("content", ""))
    finally:
        await client.close()

    print("-" * 50)
    print(f"Respuesta completa: {''.join(full_response)!r}")


if __name__ == "__main__":
    asyncio.run(main())
