from __future__ import annotations

import json

import httpx
import pytest

from creditops.infrastructure.gcp.metadata_token import (
    MetadataTokenError,
    MetadataTokenProvider,
)


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetches_and_caches_until_near_expiry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["Metadata-Flavor"] == "Google"
        return httpx.Response(
            200,
            content=json.dumps({"access_token": "token-1", "expires_in": 3600}),
        )

    clock_value = 0.0
    provider = MetadataTokenProvider(client=_client(handler), clock=lambda: clock_value)

    assert await provider() == "token-1"
    assert await provider() == "token-1"
    assert calls == 1

    clock_value = 3600.0  # past expiry minus skew
    assert await provider() == "token-1"
    assert calls == 2


@pytest.mark.asyncio
async def test_non_200_fails_closed() -> None:
    provider = MetadataTokenProvider(
        client=_client(lambda request: httpx.Response(403, content=b"denied"))
    )
    with pytest.raises(MetadataTokenError):
        await provider()


@pytest.mark.asyncio
async def test_missing_token_fails_closed() -> None:
    provider = MetadataTokenProvider(
        client=_client(
            lambda request: httpx.Response(200, content=json.dumps({"expires_in": 60}))
        )
    )
    with pytest.raises(MetadataTokenError):
        await provider()
