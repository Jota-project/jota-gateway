import logging
from src.services.protocol import OrchestratorProtocol
from src.services.orchestrators.reconnecting import (
    OrchestratorStatus,
    ReconnectingOrchestrator,
)

logger = logging.getLogger(__name__)


class OrchestratorRegistry:
    def __init__(self, clients: dict[str, ReconnectingOrchestrator]):
        self._clients = clients

    async def connect_all(self) -> None:
        for name, client in self._clients.items():
            try:
                await client.connect()
                logger.info(f"Orchestrator '{name}' connected.")
            except Exception as e:
                logger.error(f"Orchestrator '{name}' failed to connect: {e} — starting background retry")
                await client.trigger_reconnect()

    async def close_all(self) -> None:
        for name, client in self._clients.items():
            try:
                await client.close()
                logger.info(f"Orchestrator '{name}' closed.")
            except Exception as e:
                logger.warning(f"Orchestrator '{name}' error on close: {e}")

    def get(self, name: str) -> OrchestratorProtocol:
        if name not in self._clients:
            available = list(self._clients)
            raise KeyError(f"Orchestrator '{name}' not registered. Available: {available}")
        return self._clients[name]

    def default(self) -> OrchestratorProtocol:
        from src.core.config import settings
        return self.get(settings.DEFAULT_ORCHESTRATOR)

    def get_status(self, name: str) -> OrchestratorStatus:
        if name not in self._clients:
            raise KeyError(f"Orchestrator '{name}' not registered.")
        return self._clients[name].status()

    async def reconnect(self, name: str) -> None:
        if name not in self._clients:
            raise KeyError(f"Orchestrator '{name}' not registered.")
        await self._clients[name].trigger_reconnect()


def build_registry() -> OrchestratorRegistry:
    from src.core.config import settings
    from src.services.orchestrators.openclaw_client import OpenClawClient

    clients: dict[str, ReconnectingOrchestrator] = {}

    if settings.OPENCLAW_TOKEN:
        inner = OpenClawClient(
            host=settings.OPENCLAW_HOST,
            port=settings.OPENCLAW_PORT,
            token=settings.OPENCLAW_TOKEN,
            default_agent=settings.OPENCLAW_DEFAULT_AGENT,
        )
        clients["openclaw"] = ReconnectingOrchestrator(inner, name="openclaw")
        logger.info("OpenClawClient registered (wrapped in ReconnectingOrchestrator).")
    else:
        logger.warning("OPENCLAW_TOKEN not set — openclaw orchestrator not registered.")

    return OrchestratorRegistry(clients)
