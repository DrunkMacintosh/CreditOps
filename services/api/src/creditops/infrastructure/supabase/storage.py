from __future__ import annotations

import base64
import hashlib
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from creditops.application.ports.storage import (
    StorageConfigurationError,
    StorageError,
    StorageObjectMetadata,
    StorageObjectNotFound,
    StoragePort,
    UploadAuthorization,
)
from creditops.config import Settings

_PRIVATE_BUCKETS = frozenset({"creditops-incoming", "creditops-originals", "creditops-derived"})
_TUS_CHUNK_THRESHOLD = 6 * 1024 * 1024


class SupabaseStorage(StoragePort):
    """Server-only Supabase Storage adapter.

    The service-role credential is read only by this adapter and is never
    included in an ``UploadAuthorization`` returned to the browser.  The
    client is injectable for contract tests; live Supabase calls are not part
    of the local prototype verification.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (settings.supabase_url or "").rstrip("/")
        self._service_role_key = (
            settings.supabase_service_role_key.get_secret_value()
            if settings.supabase_service_role_key is not None
            else None
        )
        self._tus_url = settings.supabase_storage_tus_url or (
            f"{self._base_url}/storage/v1/upload/resumable"
        )
        self._max_upload_bytes = settings.supabase_storage_max_upload_bytes
        self._intent_ttl_seconds = settings.supabase_storage_intent_ttl_seconds
        self._client = client
        self._owns_client = client is None
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        parsed = urlsplit(self._base_url)
        if not self._base_url or parsed.scheme.lower() != "https" or not parsed.hostname:
            raise StorageConfigurationError("Supabase Storage URL is not configured")
        tus = urlsplit(self._tus_url)
        if tus.scheme.lower() != "https" or tus.hostname != parsed.hostname:
            raise StorageConfigurationError("Supabase resumable endpoint is not trusted")
        if not self._service_role_key:
            raise StorageConfigurationError("Supabase Storage server credential is not configured")

    def _admin_headers(self) -> dict[str, str]:
        self._validate_configuration()
        assert self._service_role_key is not None
        return {
            "Authorization": f"Bearer {self._service_role_key}",
            "apikey": self._service_role_key,
        }

    @staticmethod
    def _bucket_key(bucket_id: str, object_key: str) -> None:
        if bucket_id not in _PRIVATE_BUCKETS:
            raise StorageError("Storage bucket is outside the private allow-list")
        if not object_key or object_key.startswith("/") or ".." in object_key.split("/"):
            raise StorageError("Storage object path is invalid")

    def _object_url(self, bucket_id: str, object_key: str, *, info: bool = False) -> str:
        self._bucket_key(bucket_id, object_key)
        suffix = "/info" if info else ""
        return (
            f"{self._base_url}/storage/v1/object{suffix}/"
            f"{quote(bucket_id, safe='')}/{quote(object_key, safe='')}"
        )

    async def _request_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
        return self._client

    async def create_upload_authorization(
        self,
        *,
        bucket_id: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        expires_at: datetime,
    ) -> UploadAuthorization:
        self._bucket_key(bucket_id, object_key)
        if (
            bucket_id != "creditops-incoming"
            or size_bytes <= 0
            or size_bytes > self._max_upload_bytes
        ):
            raise StorageError("Storage upload is outside the configured contract")
        now = datetime.now(UTC)
        ttl = max(1, min(self._intent_ttl_seconds, int((expires_at - now).total_seconds())))
        client = await self._request_client()
        endpoint = (
            f"{self._base_url}/storage/v1/object/upload/sign/"
            f"{quote(bucket_id, safe='')}/{quote(object_key, safe='')}"
        )
        try:
            response = await client.post(
                endpoint,
                headers={**self._admin_headers(), "Content-Type": "application/json"},
                json={"expiresIn": ttl, "upsert": False},
            )
        except httpx.HTTPError as exc:
            raise StorageError("Storage authorization request failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise StorageError("Storage authorization was rejected")
        try:
            payload = response.json()
        except ValueError as exc:
            raise StorageError("Storage authorization response was invalid") from exc
        if not isinstance(payload, dict):
            raise StorageError("Storage authorization response was invalid")
        upload_url = payload.get("url") or payload.get("signedURL") or payload.get("signedUrl")
        if not isinstance(upload_url, str):
            raise StorageError("Storage authorization did not include an upload URL")
        resolved = urljoin(f"{self._base_url}/", upload_url)
        trusted = urlsplit(self._base_url)
        resolved_parts = urlsplit(resolved)
        if resolved_parts.hostname != trusted.hostname or resolved_parts.scheme != trusted.scheme:
            raise StorageError("Storage authorization URL is not trusted")
        expected_sign_path = (
            f"/storage/v1/object/upload/sign/{bucket_id}/{object_key}"
        )
        if unquote(resolved_parts.path) != expected_sign_path:
            raise StorageError("Storage authorization URL is not bound to the requested object")

        if size_bytes < _TUS_CHUNK_THRESHOLD:
            return UploadAuthorization(
                mode="SIGNED",
                upload_url=resolved,
                expires_at=expires_at,
                method="PUT",
                headers={"Content-Type": content_type, "x-upsert": "false"},
            )

        signature = payload.get("token") or payload.get("signature") or payload.get("x-signature")
        if not isinstance(signature, str) or not signature:
            raise StorageError("Storage resumable authorization did not include a signature")
        metadata = ",".join(
            (
                f"bucketName {self._b64(bucket_id)}",
                f"objectName {self._b64(object_key)}",
                f"contentType {self._b64(content_type)}",
            )
        )
        return UploadAuthorization(
            mode="RESUMABLE",
            upload_url=self._trusted_tus_url(),
            expires_at=expires_at,
            headers={
                "x-signature": signature,
                "x-upsert": "false",
                "Upload-Metadata": metadata,
            },
        )

    def _trusted_tus_url(self) -> str:
        parsed = urlsplit(self._base_url)
        tus = urlsplit(self._tus_url)
        if tus.scheme.lower() != parsed.scheme.lower() or tus.hostname != parsed.hostname:
            raise StorageError("Storage resumable endpoint is not trusted")
        return self._tus_url

    @staticmethod
    def _b64(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    async def head_object(
        self,
        *,
        bucket_id: str,
        object_key: str,
    ) -> StorageObjectMetadata:
        client = await self._request_client()
        try:
            response = await client.head(
                self._object_url(bucket_id, object_key, info=True),
                headers=self._admin_headers(),
            )
        except httpx.HTTPError as exc:
            raise StorageError("Storage metadata request failed") from exc
        if response.status_code in (404, 410):
            raise StorageObjectNotFound("Storage object was not found")
        if response.status_code < 200 or response.status_code >= 300:
            raise StorageError("Storage metadata request was rejected")
        try:
            size_bytes = int(response.headers.get("content-length", "-1"))
        except ValueError as exc:
            raise StorageError("Storage object size was invalid") from exc
        if size_bytes < 0:
            raise StorageError("Storage object size was unavailable")
        sha256 = self._sha256_header(response.headers)
        return StorageObjectMetadata(
            bucket_id=bucket_id,
            object_key=object_key,
            size_bytes=size_bytes,
            content_type=response.headers.get("content-type"),
            sha256=sha256,
        )

    @staticmethod
    def _sha256_header(headers: Mapping[str, str]) -> str | None:
        raw = headers.get("x-content-sha256") or headers.get("x-amz-meta-sha256")
        if raw is None:
            return None
        value = raw.strip().lower()
        return (
            value
            if len(value) == 64 and all(char in "0123456789abcdef" for char in value)
            else None
        )

    async def _stream_object(
        self,
        *,
        bucket_id: str,
        object_key: str,
    ) -> AsyncIterator[bytes]:
        client = await self._request_client()
        try:
            async with client.stream(
                "GET",
                self._object_url(bucket_id, object_key),
                headers=self._admin_headers(),
            ) as response:
                if response.status_code in (404, 410):
                    raise StorageObjectNotFound("Storage object was not found")
                if response.status_code < 200 or response.status_code >= 300:
                    raise StorageError("Storage object download was rejected")
                async for chunk in response.aiter_bytes(1024 * 1024):
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise StorageError("Storage object download failed") from exc

    def open_object(self, *, bucket_id: str, object_key: str) -> AsyncIterator[bytes]:
        return self._stream_object(bucket_id=bucket_id, object_key=object_key)

    async def _verify_immutable_destination(
        self,
        *,
        bucket_id: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        content_sha256: str,
    ) -> None:
        existing = await self.head_object(bucket_id=bucket_id, object_key=object_key)
        if existing.size_bytes != size_bytes:
            raise StorageError("Immutable destination has a different size")
        if existing.content_type and existing.content_type.lower() != content_type.lower():
            raise StorageError("Immutable destination has a different content type")
        if existing.sha256 and existing.sha256 != content_sha256:
            raise StorageError("Immutable destination has a different checksum")
        if existing.sha256 is None:
            digest = hashlib.sha256()
            observed = 0
            async for chunk in self.open_object(bucket_id=bucket_id, object_key=object_key):
                observed += len(chunk)
                if observed > size_bytes:
                    raise StorageError("Immutable destination exceeds expected size")
                digest.update(chunk)
            if observed != size_bytes or digest.hexdigest() != content_sha256:
                raise StorageError("Immutable destination checksum could not be verified")

    async def copy_immutable(
        self,
        *,
        source_bucket: str,
        source_key: str,
        destination_bucket: str,
        destination_key: str,
        content_type: str,
        size_bytes: int,
        content_sha256: str,
    ) -> None:
        self._bucket_key(source_bucket, source_key)
        self._bucket_key(destination_bucket, destination_key)
        if destination_bucket != "creditops-originals":
            raise StorageError("Immutable destination bucket is not allowed")
        try:
            await self._verify_immutable_destination(
                bucket_id=destination_bucket,
                object_key=destination_key,
                content_type=content_type,
                size_bytes=size_bytes,
                content_sha256=content_sha256,
            )
            return
        except StorageObjectNotFound:
            pass
        client = await self._request_client()
        try:
            response = await client.post(
                f"{self._base_url}/storage/v1/object/copy",
                headers={**self._admin_headers(), "Content-Type": "application/json"},
                json={
                    "bucketId": source_bucket,
                    "sourceKey": source_key,
                    "destinationBucket": destination_bucket,
                    "destinationKey": destination_key,
                    "contentType": content_type,
                    "contentLength": size_bytes,
                    "contentSha256": content_sha256,
                    "upsert": False,
                },
            )
        except httpx.HTTPError as exc:
            raise StorageError("Storage immutable copy failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise StorageError("Storage immutable copy was rejected")
        await self._verify_immutable_destination(
            bucket_id=destination_bucket,
            object_key=destination_key,
            content_type=content_type,
            size_bytes=size_bytes,
            content_sha256=content_sha256,
        )

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
