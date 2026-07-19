"""Safe logging helpers for secrets and credentials."""
import hashlib


def fingerprint_key(value: str) -> str:
    """Return a non-reversible eight-character SHA-256 fingerprint."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
