from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from creditops.application.ports.storage import StorageError, StorageObjectNotFound
from creditops.config import Settings
from creditops.infrastructure.supabase.storage import SupabaseStorage

# The exact JSON body live Supabase Storage returns from ``GET /object/info``
# (captured 2026-07-19, correlation c416bcf9): the object's true size and type
# live in the body -- ``size`` and ``content_type`` -- never in the HTTP headers.
_LIVE_INFO_BODY = {
    "id": "06d29833-42b8-4ee1-b140-1af7b65cabf2",
    "name": "incoming/case/intent",
    "version": "992abe22-a7dc-4713-a6e0-fab9099de1ff",
    "bucket_id": "creditops-incoming",
    "size": 5,
    "content_type": "application/pdf",
    "cache_control": "no-cache",
    "etag": '"5d41402abc4b2a76b9719d911017c592"',
    "metadata": {},
    "last_modified": "2026-07-19T01:27:29.403Z",
    "created_at": "2026-07-19T01:27:29.403Z",
}


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


@pytest.mark.asyncio
async def test_signed_authorization_accepts_live_service_root_relative_url() -> None:
    # Regression: the LIVE Storage API (observed 2026-07-18 against
    # tosvjtnqmsbyjonjacsn.supabase.co) returns the signed url relative to its
    # own service root — "/object/upload/sign/..." WITHOUT the "/storage/v1"
    # prefix, plus a ?token= query. The adapter must re-anchor it, not reject
    # Supabase's own valid response as untrusted.
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "url": "/object/upload/sign/creditops-incoming/incoming/case/intent?token=tok123",
                "token": "tok123",
            },
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
    assert result.upload_url.startswith(
        "https://project.supabase.co/storage/v1/object/upload/sign/creditops-incoming/"
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_service_root_relative_url_still_rejects_foreign_object() -> None:
    # Re-anchoring must not weaken the binding check: a prefixless url that
    # points at a DIFFERENT object is still rejected.
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"url": "/object/upload/sign/creditops-incoming/incoming/other?token=t"},
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


@pytest.mark.asyncio
async def test_head_object_reads_size_and_type_from_info_json_not_headers() -> None:
    # Regression (live 502 STORAGE_VERIFICATION_FAILED, correlation c416bcf9):
    # the adapter must GET ``/object/info`` and read the object's real size/type
    # from the JSON body. A HEAD there would instead surface the metadata
    # document's own ``content-length`` (363) and ``application/json`` header.
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        # ``json=`` sets the HTTP ``content-type: application/json`` header and a
        # header ``content-length`` of the *document* -- both deliberately wrong
        # for the object; the adapter must ignore them and trust the body.
        return httpx.Response(200, json=_LIVE_INFO_BODY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseStorage(settings(), client=client)
    metadata = await adapter.head_object(
        bucket_id="creditops-incoming", object_key="incoming/case/intent"
    )

    assert requests[0].method == "GET"
    assert "object/info/creditops-incoming" in str(requests[0].url)
    assert metadata.size_bytes == 5
    assert metadata.content_type == "application/pdf"
    assert metadata.sha256 is None
    await client.aclose()


@pytest.mark.asyncio
async def test_head_object_missing_object_raises_not_found() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseStorage(settings(), client=client)
    with pytest.raises(StorageObjectNotFound):
        await adapter.head_object(
            bucket_id="creditops-incoming", object_key="incoming/case/intent"
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_head_object_without_a_size_fails_closed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = {k: v for k, v in _LIVE_INFO_BODY.items() if k != "size"}
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseStorage(settings(), client=client)
    with pytest.raises(StorageError):
        await adapter.head_object(
            bucket_id="creditops-incoming", object_key="incoming/case/intent"
        )
    await client.aclose()
