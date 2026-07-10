from src.services.reconnection import ConnectionState, ServiceStatus, to_wire_state


def test_to_wire_state_mapping():
    assert to_wire_state(ConnectionState.CONNECTED) == "restored"
    assert to_wire_state(ConnectionState.RECONNECTING) == "reconnecting"
    assert to_wire_state(ConnectionState.DEGRADED) == "unavailable"


def test_service_status_is_a_plain_dataclass():
    s = ServiceStatus(name="x", state=ConnectionState.CONNECTED, connected_at=None,
                       reconnect_attempts=0, last_error=None)
    assert s.name == "x"
    assert s.state == ConnectionState.CONNECTED
