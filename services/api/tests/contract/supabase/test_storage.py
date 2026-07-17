from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from creditops.application.ports.storage import StorageError
from creditops.config import Settings
from creditops.infrastructure.supabase.storage import SupabaseStorage


def settings() -> Settings:
    return Settings(
        app_env="test",
        supabase_url="https://project.supabase.co",
        supabase_service_role_key=SecretStr("service-role-secret"),
    )


@pytest.mark.asyncio
async def test_signed_authorization_binds_private_path_without_service_key() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"url": "/storage/v1/object/upload/sign/creditops-incoming/incoming/case/intent"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseStorage(settings(), client=client)
    result = await adapter.create_upload_authorization(
        bucket_id="creditops-incoming",
        object_key="incoming/case/intent",
        content_type="application/pdf",
        size_bytes=100,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    assert result.mode == "SIGNED"
    assert result.headers["x-upsert"] == "false"
    assert all("service-role-secret" not in value for value in result.headers.values())
    assert requests[0].headers["authorization"] == "Bearer service-role-secret"
    assert requests[0].content and b'"upsert":false' in requests[0].content
    await client.aclose()


@pytest.mark.asyncio
async def test_large_upload_returns_signed_tus_metadata_and_no_admin_header() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "url": "/storage/v1/object/upload/sign/creditops-incoming/incoming/case/intent",
                "token": "short-lived-signature",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseStorage(settings(), client=client)
    result = await adapter.create_upload_authorization(
        bucket_id="creditops-incoming",
        object_key="incoming/case/intent",
        content_type="application/pdf",
        size_bytes=6 * 1024 * 1024,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    assert result.mode == "RESUMABLE"
    assert result.headers["x-signature"] == "short-lived-signature"
    assert "bucketName" in result.headers["Upload-Metadata"]
    assert "objectName" in result.headers["Upload-Metadata"]
    assert "contentType" in result.headers["Upload-Metadata"]
    assert "Authorization" not in result.headers
    await client.aclose()


@pytest.mark.asyncio
async def test_untrusted_signed_url_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"url": "https://evil.example/upload"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseStorage(settings(), client=client)
    with pytest.raises(StorageError):
        await adapter.create_upload_authorization(
            bucket_id="creditops-incoming",
            object_key="incoming/case/intent",
            content_type="application/pdf",
            size_bytes=100,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_signed_url_for_a_different_object_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "url": "/storage/v1/object/upload/sign/creditops-incoming/incoming/other"
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseStorage(settings(), client=client)
    with pytest.raises(StorageError):
        await adapter.create_upload_authorization(
            bucket_id="creditops-incoming",
            object_key="incoming/case/intent",
            content_type="application/pdf",
            size_bytes=100,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    await client.aclose()
