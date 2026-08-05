import hashlib
import re

from src.core.logging import fingerprint_key


def test_fingerprint_key_is_sha256_prefix():
    key = "valid-key-abc"

    assert fingerprint_key(key) == hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def test_fingerprint_key_is_eight_lowercase_hex_characters():
    assert re.fullmatch(r"[0-9a-f]{8}", fingerprint_key("another-secret"))


def test_fingerprint_key_newline_input_cannot_break_log_line():
    key = "bad-key\nforged-log-entry"
    fingerprint = fingerprint_key(key)

    assert "\n" not in fingerprint
    assert key not in fingerprint
    assert "bad-key" not in fingerprint
