"""Signed work-order authorization tokens.

A token is ``<payload>.<signature>``, both base64url-encoded without padding.
The payload is compact JSON; the signature is HMAC-SHA256 over the encoded
payload using a server-side secret. Anyone holding the token can read the
payload, but nobody can change it without the secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

TOKEN_VERSION = 1


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


class TokenError(ValueError):
    """Raised when a token is malformed or its signature does not match."""


@dataclass(frozen=True)
class Signer:
    secret: bytes

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise ValueError("Signing secret must be at least 32 bytes.")

    def sign(self, payload: dict[str, Any]) -> str:
        body = dict(payload)
        body["v"] = TOKEN_VERSION
        encoded = _b64e(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        sig = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{_b64e(sig)}"

    def verify(self, token: str) -> dict[str, Any]:
        """Return the payload if the signature is valid, else raise TokenError."""
        if not isinstance(token, str) or token.count(".") != 1:
            raise TokenError("Token is malformed.")
        encoded, sig_text = token.strip().split(".")
        try:
            given_sig = _b64d(sig_text)
        except (ValueError, TypeError) as exc:
            raise TokenError("Token is malformed.") from exc
        expected = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(given_sig, expected):
            raise TokenError("Signature does not match. The token was altered or not issued by this server.")
        try:
            payload = json.loads(_b64d(encoded))
        except (ValueError, TypeError) as exc:
            raise TokenError("Token payload is unreadable.") from exc
        if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION:
            raise TokenError("Unsupported token version.")
        return payload
