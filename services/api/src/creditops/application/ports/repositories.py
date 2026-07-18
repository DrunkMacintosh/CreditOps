from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


class ForbiddenError(PermissionError):
    """The actor does not have an active assignment for the case."""


class InsufficientRoleError(PermissionError):
    """The actor does not hold the bounded intake role."""


@dataclass(frozen=True, slots=True)
class CaseRecord:
    id: UUID
    version: int
    assigned_officer_id: UUID
    requested_amount: str
    purpose_vi: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEvent:
    case_id: UUID
    case_version: int
    event_type: str
    actor_id: UUID
    artifact_type: str
    artifact_id: UUID
    event_data: Mapping[str, object]
    request_id: str


class CaseRepository(Protocol):
    async def create(
        self,
        *,
        actor_id: UUID,
        assigned_officer_id: UUID,
        requested_amount: str,
        purpose_vi: str,
    ) -> CaseRecord: ...

    async def require_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord: ...

    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None: ...

    async def list_assigned(
        self,
        actor_id: UUID,
        *,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[CaseRecord], UUID | None]: ...

    async def list_assignment_roles(
        self,
        case_id: UUID,
        actor_id: UUID,
    ) -> frozenset[str]: ...


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class UploadIntentRecord:
    id: UUID
    case_id: UUID
    case_version: int
    assigned_officer_id: UUID
    bucket_id: str
    object_key: str
    original_filename: str
    accepted_content_type: str
    declared_size_bytes: int
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    actor_id: UUID
    operation: str
    idempotency_key: str
    request_sha256: str
    id: UUID | None = None
    case_id: UUID | None = None
    lease_owner: UUID | None = None
    lease_until: datetime | None = None
    completed_at: datetime | None = None
    response_status: int | None = None
    response_data: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class UploadRegistration:
    document_id: UUID
    document_version_id: UUID
    task_id: UUID
    task_status: str


class UploadRepository(Protocol):
    async def create_intent(
        self,
        *,
        intent_id: UUID,
        case: CaseRecord,
        original_filename: str,
        content_type: str,
        declared_size_bytes: int,
        bucket_id: str,
        object_key: str,
        expires_at: datetime,
    ) -> UploadIntentRecord: ...

    async def get_intent(
        self,
        intent_id: UUID,
        actor_id: UUID,
    ) -> UploadIntentRecord | None: ...

    async def find_idempotency(
        self,
        *,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> IdempotencyRecord | None: ...

    async def reserve_idempotency(
        self,
        *,
        case_id: UUID,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
        request_sha256: str,
        lease_until: datetime,
        lease_owner: UUID,
    ) -> IdempotencyRecord: ...

    async def complete_idempotency(
        self,
        *,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
        response_status: int,
        response_data: Mapping[str, object],
        lease_owner: UUID,
    ) -> None: ...

    async def find_duplicate_document(
        self,
        *,
        case_id: UUID,
        content_sha256: str,
    ) -> UUID | None: ...

    async def register_verified_upload(
        self,
        *,
        intent: UploadIntentRecord,
        immutable_bucket: str,
        immutable_key: str,
        detected_content_type: str,
        content_sha256: str,
        task_input: Mapping[str, object],
        actor_id: UUID,
    ) -> UploadRegistration: ...

    async def consume_intent(
        self,
        *,
        intent_id: UUID,
        actor_id: UUID,
        consumed_at: datetime,
        completion_idempotency_record_id: UUID,
    ) -> None: ...
