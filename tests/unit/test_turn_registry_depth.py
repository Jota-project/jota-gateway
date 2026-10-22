"""#219: profundidad y máximo de la cola de un turno."""

from src.services.openclaw.registry import TurnRegistry


def test_turn_registry_depth_and_peak():
    reg = TurnRegistry()
    q = reg.register("r1", "sk")
    for i in range(3):
        reg.deliver_by_session("sk", ("chat", i))
    assert reg.depth("sk") == (3, 3)
    for _ in range(3):
        q.get_nowait()
    reg.unregister("sk", "r1")
    assert reg.depth("sk") == (0, 3)
    assert reg.pop_peak("sk") == 3
    assert reg.depth("sk") == (0, 0)
