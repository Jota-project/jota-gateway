from src.core.session_key import make_session_key


def test_ws_session_key():
    assert make_session_key("assistant", "hab_sito") == "agent:assistant:hab_sito"


def test_http_session_key():
    assert make_session_key("main", "ha") == "agent:main:ha"


def test_empty_agent_still_formats():
    assert make_session_key("", "hab_sito") == "agent::hab_sito"
