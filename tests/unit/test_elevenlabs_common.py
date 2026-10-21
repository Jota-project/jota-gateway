import logging
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close

from src.services.speech.elevenlabs.common import (
    classify_close,
    classify_exception,
    https_base,
    pin_websockets_logging,
    ws_url,
)
from src.services.speech.factory import build_tts_provider


def test_ws_url_and_https_base():
    assert (
        ws_url("wss://api.elevenlabs.io/", "/v1/text-to-speech/abc/stream-input", {"a": "b c", "n": 1})
        == "wss://api.elevenlabs.io/v1/text-to-speech/abc/stream-input?a=b+c&n=1"
    )
    assert ws_url("127.0.0.1:9", "/x") == "wss://127.0.0.1:9/x"
    assert https_base("wss://api.elevenlabs.io/") == "https://api.elevenlabs.io"
    assert https_base("ws://127.0.0.1:9") == "http://127.0.0.1:9"


@pytest.mark.parametrize(
    ("code", "reason", "kind"),
    [
        (1008, "Invalid API key", "auth"),
        (1008, "", "other"),
        (1008, "Input timeout exceeded", "other"),
        (1008, "permission denied", "auth"),
        (4001, "invalid key", "auth"),
        (1011, "quota_exceeded", "quota"),
        (1011, "Insufficient credits", "quota"),
        (1011, "insufficient balance", "quota"),
        (1008, "session time limit exceeded", "other"),
        (1008, "session_time_limit_exceeded", "other"),
        (1013, "Server busy", "capacity"),
        (1008, "concurrent limit", "capacity"),
        (1011, "boom", "other"),
        (None, None, "other"),
    ],
)
def test_classify_close(code, reason, kind):
    assert classify_close(code, reason) == kind


def _status(code):
    return InvalidStatus(SimpleNamespace(status_code=code))


def test_classify_exception():
    assert classify_exception(_status(401)) == "auth"
    assert classify_exception(_status(403)) == "auth"
    assert classify_exception(_status(429)) == "capacity"
    assert classify_exception(_status(500)) == "other"
    assert classify_exception(ConnectionClosedError(Close(1008, "Invalid API key"), None)) == "auth"
    assert classify_exception(ConnectionRefusedError()) == "other"


def test_pin_websockets_logging_idempotent_and_never_lowers():
    lg = logging.getLogger("websockets")
    old = lg.level
    try:
        lg.setLevel(logging.NOTSET)
        pin_websockets_logging()
        pin_websockets_logging()
        assert lg.level == logging.INFO
        lg.setLevel(logging.ERROR)
        pin_websockets_logging()
        assert lg.level == logging.ERROR
    finally:
        lg.setLevel(old)


def test_factory_builds_elevenlabs_tts_and_stt():
    from pydantic import SecretStr

    from src.core.config import settings

    s = settings.model_copy(update={"ELEVENLABS_API_KEY": SecretStr("k")})
    assert build_tts_provider(s, "elevenlabs").name == "elevenlabs"
    from src.services.speech.factory import build_stt_provider

    assert build_stt_provider(s, "elevenlabs").name == "elevenlabs"
    with pytest.raises(ValueError, match="no soportado"):
        build_stt_provider(s, "nope")
