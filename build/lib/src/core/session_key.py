def make_session_key(agent: str, client_id: str) -> str:
    return f"agent:{agent}:{client_id}"
