"""OAuth access tokens from the Cloud Run metadata server.

On Cloud Run the metadata server mints access tokens for the service's own
service account; no key material ever touches the codebase.  Off-cloud the
fetch fails and the caller (CloudRunDispatcher) surfaces
``WorkerDispatchError`` — the queue message stays durable for the scheduled
recovery sweep, so a missing dispatcher can never lose work.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/token"
)
_EXPIRY_SKEW_SECONDS = 60.0


class MetadataTokenError(RuntimeError):
    """The metadata server did not return a usable access token."""


class MetadataTokenProvider:
    """Fetch and cache the service account's access token until near expiry."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._client = client
        self._token: str | None = None
        self._expires_at = 0.0
        self._clock: Callable[[], float] = clock or time.monotonic

    async def __call__(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._expires_at:
            return self._token
        client = self._client or httpx.AsyncClient(timeout=5.0)
        owns_client = self._client is None
        try:
            response = await client.get(
                _METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"}
            )
        except httpx.HTTPError as exc:
            raise MetadataTokenError("metadata server is unreachable") from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code != 200:
            raise MetadataTokenError(
                f"metadata server returned status {response.status_code}"
            )
        payload = response.json()
        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(token, str) or not token:
            raise MetadataTokenError("metadata server returned no access token")
        self._token = token
        lifetime = float(expires_in) if isinstance(expires_in, int | float) else 0.0
        self._expires_at = now + max(lifetime - _EXPIRY_SKEW_SECONDS, 0.0)
        return token
