"""This laptop's network addresses, which other devices use to reach the server."""

import socket

CHECK_TIMEOUT_SECONDS = 0.3


def lan_addresses() -> list[str]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({info[4][0] for info in infos if not info[4][0].startswith("127.")})


def accepts_connections(address: str, port: int) -> bool:
    """False when the server was started for this laptop only (127.0.0.1), so phones would time
    out. This can't see the firewall, because a connection to yourself doesn't pass through it."""
    try:
        with socket.create_connection((address, port), timeout=CHECK_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False
