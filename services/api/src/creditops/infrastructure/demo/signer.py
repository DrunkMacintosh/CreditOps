"""RS256 signer for the synthetic anonymous demo session.

The demo session is signed by a LOCAL RSA key (configured PEM), never by a
real identity provider.  This module owns two responsibilities:

1. mint a short-TTL RS256 JWT the demo endpoint hands back to the BFF, and
2. derive the matching PUBLIC JWKS document so the API's own
   :class:`~creditops.api.auth.JwtVerifier` (seeded from a local
   :class:`~creditops.api.auth.JwksKeyResolver`) validates exactly the tokens
   this signer produced.

The private key stays inside this object; only the public JWKS ever leaves it.
No secret material is placed in a minted token, a JWKS document, or a log line.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from jwt.algorithms import RSAAlgorithm

__all__ = ["DemoJwtSigner", "DemoSignerError"]

_MIN_RSA_KEY_BITS = 2048


class DemoSignerError(RuntimeError):
    """The demo signer could not be built from the supplied configuration."""


class DemoJwtSigner:
    """Sign short-TTL RS256 demo JWTs and expose the matching public JWKS."""

    def __init__(
        self,
        *,
        private_key_pem: str,
        issuer: str,
        audience: str,
        kid: str,
        ttl_seconds: int,
    ) -> None:
        if ttl_seconds <= 0:
            raise DemoSignerError("demo session TTL must be positive")
        if not issuer or not audience or not kid:
            raise DemoSignerError("demo issuer, audience and kid must be configured")
        try:
            key = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        except (ValueError, TypeError) as exc:
            raise DemoSignerError("demo JWT private key is not a valid PEM key") from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise DemoSignerError("demo JWT private key must be an RSA private key")
        if key.key_size < _MIN_RSA_KEY_BITS:
            raise DemoSignerError(
                f"demo JWT private key must be at least {_MIN_RSA_KEY_BITS} bits"
            )
        self._private_key = key
        self._issuer = issuer
        self._audience = audience
        self._kid = kid
        self._ttl_seconds = ttl_seconds

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def audience(self) -> str:
        return self._audience

    @property
    def kid(self) -> str:
        return self._kid

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def sign(
        self,
        *,
        subject: UUID,
        roles: Sequence[str],
        now: datetime | None = None,
    ) -> tuple[str, int]:
        """Return ``(token, expires_in_seconds)`` for the synthetic actor."""

        issued_at = now if now is not None else datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=self._ttl_seconds)
        token = jwt.encode(
            {
                "iss": self._issuer,
                "aud": self._audience,
                "sub": str(subject),
                "roles": list(roles),
                "iat": issued_at,
                "exp": expires_at,
            },
            self._private_key,
            algorithm="RS256",
            headers={"kid": self._kid},
        )
        return token, self._ttl_seconds

    def public_jwks(self) -> dict[str, Any]:
        """Derive the PUBLIC JWKS document for the API's local key resolver.

        Only public RSA parameters are exported; the private key never leaves
        this object.
        """

        jwk: dict[str, Any] = RSAAlgorithm.to_jwk(self._private_key.public_key(), as_dict=True)
        jwk.update({"kid": self._kid, "alg": "RS256", "use": "sig"})
        return {"keys": [jwk]}
