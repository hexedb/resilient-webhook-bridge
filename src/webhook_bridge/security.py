from __future__ import annotations

import hashlib
import hmac
import time


class InvalidSignature(ValueError):
    """Raised when a webhook signature cannot be trusted."""


def sign_payload(secret: str, body: bytes, timestamp: int) -> str:
    message = str(timestamp).encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(
    secret: str,
    body: bytes,
    timestamp: int,
    signature: str,
    *,
    max_age_seconds: int = 300,
    now: int | None = None,
) -> None:
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > max_age_seconds:
        raise InvalidSignature("signature timestamp is outside the allowed clock skew")
    expected = sign_payload(secret, body, timestamp)
    if not hmac.compare_digest(expected, signature):
        raise InvalidSignature("signature mismatch")

