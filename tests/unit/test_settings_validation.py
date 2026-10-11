"""CLIENT_SEND_TIMEOUT_S must be strictly positive (issue #130 S2)."""

import pytest
from pydantic import ValidationError

from src.core.config import Settings


@pytest.mark.parametrize("value", [0, -1])
def test_client_send_timeout_rejects_non_positive(value):
    with pytest.raises(ValidationError):
        Settings(CLIENT_SEND_TIMEOUT_S=value)


def test_client_send_timeout_accepts_small_positive():
    assert Settings(CLIENT_SEND_TIMEOUT_S=0.5).CLIENT_SEND_TIMEOUT_S == 0.5
