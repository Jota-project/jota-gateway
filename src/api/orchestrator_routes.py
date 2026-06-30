from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.deps import get_verified_client
from src.models.schemas import Client, ClientConfig

router = APIRouter()


@router.get("/orchestrators/{name}/status")
async def get_orchestrator_status(
    name: str,
    request: Request,
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> dict:
    openclaw = request.app.state.openclaw
    if name != openclaw._name:
        raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
    s = openclaw.status()
    return {
        "name": s.name,
        "state": s.state.value,
        "connected_at": s.connected_at.isoformat() if s.connected_at else None,
        "disconnected_at": None,
        "reconnect_attempts": s.reconnect_attempts,
        "last_error": s.last_error,
    }


@router.post("/orchestrators/{name}/reconnect", status_code=202)
async def post_orchestrator_reconnect(
    name: str,
    request: Request,
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> dict:
    openclaw = request.app.state.openclaw
    if name != openclaw._name:
        raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
    await openclaw.connect()
    return {"accepted": True}
