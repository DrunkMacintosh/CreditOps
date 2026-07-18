from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Annotated, Any, Protocol, cast
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from creditops.api.errors import ApiException
from creditops.application.unit_of_work import ActorContext

__all__ = [
    "ActorContext",
    "JwksKeyResolver",
    "JwtVerifier",
    "RemoteJwksKeyResolver",
    "require_actor",
]

_bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


class AuthenticationError(ValueError):
    """A bearer token could not be authenticated."""


class SigningKeyResolver(Protocol):
    async def resolve(self, token: str) -> Any: ...


class JwksKeyResolver:
    """Resolve RS256 signing keys from an already retrieved JWKS document."""

    def __init__(self, jwks: Mapping[str, object]) -> None:
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise ValueError("JWKS must contain a keys array")

        parsed: dict[str, Any] = {}
        for value in keys:
            if not isinstance(value, dict):
                continue
            kid = value.get("kid")
            if (
                isinstance(kid, str)
                and value.get("kty") == "RSA"
                and value.get("alg", "RS256") == "RS256"
                and value.get("use", "sig") == "sig"
            ):
                parsed[kid] = jwt.PyJWK.from_dict(value, algorithm="RS256").key
        if not parsed:
            raise ValueError("JWKS does not contain an RS256 signing key")
        self._keys = parsed

    async def resolve(self, token: str) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError from exc
        if header.get("alg") != "RS256":
            raise AuthenticationError
        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self._keys:
            raise AuthenticationError
        return self._keys[kid]


class RemoteJwksKeyResolver:
    """Resolve and cache keys from the configured identity-provider JWKS endpoint."""

    def __init__(self, jwks_url: str) -> None:
        self._client = jwt.PyJWKClient(
            jwks_url,
            cache_jwk_set=True,
            cache_keys=True,
            lifespan=300,
            timeout=5,
        )

    async def resolve(self, token: str) -> Any:
        try:
            signing_key = await asyncio.to_thread(self._client.get_signing_key_from_jwt, token)
        except (jwt.PyJWTError, OSError, ValueError) as exc:
            raise AuthenticationError from exc
        if signing_key.algorithm_name != "RS256":
            raise AuthenticationError
        return signing_key.key


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_resolver: SigningKeyResolver,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._key_resolver = key_resolver

    async def verify(self, token: str, *, request_id: str) -> ActorContext:
        try:
            key = await self._key_resolver.resolve(token)
            payload = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
            subject = UUID(cast(str, payload["sub"]))
            raw_roles = payload.get("roles", [])
            if not isinstance(raw_roles, list) or not all(
                isinstance(role, str) and 0 < len(role) <= 100 for role in raw_roles
            ):
                raise AuthenticationError
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError from exc

        return ActorContext(
            actor_id=subject,
            roles=frozenset(cast(list[str], raw_roles)),
            request_id=request_id,
        )


#: App session token header.  In production the BFF sends the Google Cloud Run
#: OIDC id token in the standard ``Authorization`` header (so the Cloud Run
#: ``--no-allow-unauthenticated`` invoker check passes) and the CreditOps app
#: session JWT here, so the two identities never collide.
APP_AUTHORIZATION_HEADER = "X-CreditOps-Authorization"


def _extract_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    """Prefer the app-session header, falling back to standard Authorization.

    The dedicated ``X-CreditOps-Authorization`` header carries the app session
    JWT when the standard ``Authorization`` header is already consumed by the
    Cloud Run invoker's OIDC id token.  When it is absent the behaviour is
    exactly the legacy single-header path, so existing callers are unaffected.
    """

    app_header = request.headers.get(APP_AUTHORIZATION_HEADER)
    if app_header is not None:
        scheme, _, token = app_header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    return credentials.credentials


async def require_actor(
    request: Request,
    credentials: BearerCredentials,
) -> ActorContext:
    token = _extract_bearer_token(request, credentials)
    if token is None:
        raise ApiException(
            status_code=401,
            code="AUTHENTICATION_REQUIRED",
            message_vi="Yêu cầu xác thực hợp lệ.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    verifier = getattr(request.app.state, "jwt_verifier", None)
    if not isinstance(verifier, JwtVerifier):
        raise ApiException(
            status_code=503,
            code="AUTHENTICATION_UNAVAILABLE",
            message_vi="Dịch vụ xác thực chưa sẵn sàng.",
            retryable=True,
        )

    try:
        return await verifier.verify(
            token,
            request_id=request.state.correlation_id,
        )
    except AuthenticationError as exc:
        raise ApiException(
            status_code=401,
            code="AUTHENTICATION_REQUIRED",
            message_vi="Yêu cầu xác thực hợp lệ.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
