class ClientNotFound(Exception):
    """Raised when client_key does not exist in the local DB."""


class ClientInactive(Exception):
    """Raised when the client exists but is_active=False."""
