"""GitHub webhook signature verification."""

import hashlib
import hmac


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Return whether a GitHub ``sha256`` signature matches the request body."""
    if not header or not header.startswith("sha256="):
        return False

    signature = header.removeprefix("sha256=")
    if len(signature) != hashlib.sha256().digest_size * 2:
        return False
    try:
        supplied = bytes.fromhex(signature)
    except ValueError:
        return False

    expected = hmac.digest(secret.encode(), body, hashlib.sha256)
    return hmac.compare_digest(expected, supplied)
