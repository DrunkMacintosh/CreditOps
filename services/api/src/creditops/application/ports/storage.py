from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


class StorageError(RuntimeError):
    """A private Storage operation failed without exposing provider details."""


class StorageConfigurationError(StorageError):
    """The server is not configured with the private Storage credentials."""


class StorageObjectNotFound(StorageError):
    """The exact intent object is not present in private Storage."""


class StorageObjectMismatch(StorageError):
    """Storage metadata does not match the upload intent."""


@dataclass(frozen=True, slots=True)
class UploadAuthorization:
    mode: Literal["SIGNED", "RESUMABLE"]
    upload_url: str
    expires_at: datetime
    headers: Mapping[str, str]
    method: Literal["POST", "PUT"] | None = None


@dataclass(frozen=True, slots=True)
class StorageObjectMetadata:
    bucket_id: str
    object_key: str
    size_bytes: int
    content_type: str | None
    sha256: str | None = None


class StoragePort(Protocol):
    async def create_upload_authorization(
        self,
        *,
        bucket_id: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        expires_at: datetime,
    ) -> UploadAuthorization: ...

    async def head_object(
        self,
        *,
        bucket_id: str,
        object_key: str,
    ) -> StorageObjectMetadata: ...

    def open_object(
        self,
        *,
        bucket_id: str,
        object_key: str,
    ) -> AsyncIterator[bytes]: ...

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
    ) -> None: ...
