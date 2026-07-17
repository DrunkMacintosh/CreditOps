from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from creditops.application.ports.repositories import (
    AuditEvent,
    IdempotencyRecord,
    UploadIntentRecord,
)
from creditops.application.ports.storage import (
    StorageObjectNotFound,
    StoragePort,
)
from creditops.application.unit_of_work import ActorContext, UnitOfWorkFactory
from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE

_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,200}$")
_IDEMPOTENCY_OPERATION = "COMPLETE_UPLOAD_INTENT_V1"
_IDEMPOTENCY_LEASE = timedelta(minutes=5)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UploadCompletionError(ValueError):
    """The upload cannot be registered from its current Storage state."""


class UploadIntentNotFound(UploadCompletionError):
    pass


class UploadIntentExpired(UploadCompletionError):
    pass


class IdempotencyKeyReused(UploadCompletionError):
    pass


class IdempotencyInProgress(UploadCompletionError):
    pass


@dataclass(frozen=True, slots=True)
class DuplicateUpload:
    outcome: str
    duplicate_of_document_id: UUID


@dataclass(frozen=True, slots=True)
class RegisteredUpload:
    outcome: str
    document_id: UUID
    document_version_id: UUID
    task_id: UUID
    task_status: str


CompleteUploadResult = DuplicateUpload | RegisteredUpload


def _request_hash(intent_id: UUID) -> str:
    return hashlib.sha256(f"complete-upload:{intent_id}:{{}}".encode("ascii")).hexdigest()


async def _sha256_stream(storage: StoragePort, intent: UploadIntentRecord) -> str:
    digest = hashlib.sha256()
    observed = 0
    async for chunk in storage.open_object(
        bucket_id=intent.bucket_id,
        object_key=intent.object_key,
    ):
        if not isinstance(chunk, bytes):
            raise UploadCompletionError("storage returned a non-byte stream")
        observed += len(chunk)
        if observed > intent.declared_size_bytes:
            raise UploadCompletionError("streamed object is larger than the declared size")
        digest.update(chunk)
    if observed != intent.declared_size_bytes:
        raise UploadCompletionError("streamed object size does not match the declared size")
    return digest.hexdigest()


def _result_from_idempotency(record: IdempotencyRecord) -> CompleteUploadResult | None:
    data = record.response_data
    if record.response_status is None or data is None:
        return None
    outcome = data.get("outcome")
    if outcome == "DUPLICATE":
        value = data.get("duplicateOfDocumentId")
        if isinstance(value, str):
            return DuplicateUpload(outcome="DUPLICATE", duplicate_of_document_id=UUID(value))
    if outcome == "REGISTERED":
        document_id = data.get("documentId")
        version_id = data.get("documentVersionId")
        task = data.get("task")
        if isinstance(document_id, str) and isinstance(version_id, str) and isinstance(task, dict):
            task_id = task.get("id")
            task_status = task.get("status")
            if isinstance(task_id, str) and isinstance(task_status, str):
                return RegisteredUpload(
                    outcome="REGISTERED",
                    document_id=UUID(document_id),
                    document_version_id=UUID(version_id),
                    task_id=UUID(task_id),
                    task_status=task_status,
                )
    raise UploadCompletionError("stored idempotency response is invalid")


class CompleteUploadIntent:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: StoragePort,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    async def execute(
        self,
        actor: ActorContext,
        intent_id: UUID,
        idempotency_key: str,
    ) -> CompleteUploadResult:
        if INTAKE_OFFICER_ROLE not in actor.roles:
            raise UploadCompletionError("intake officer role is required")
        if not _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise UploadCompletionError("idempotency key is invalid")

        now = self._now()
        request_sha256 = _request_hash(intent_id)
        lease_owner = uuid4()

        # Phase 1 is deliberately short: claim a reclaimable idempotency lease
        # and leave the database transaction before any provider I/O.  A crash
        # during Storage verification therefore leaves a retryable lease, not
        # an open database transaction.
        async with self._uow_factory(actor) as uow:
            intent = await uow.uploads.get_intent(intent_id, actor.actor_id)
            if intent is None:
                raise UploadIntentNotFound("upload intent was not found")
            existing = await uow.uploads.find_idempotency(
                actor_id=actor.actor_id,
                operation=_IDEMPOTENCY_OPERATION,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                if existing.request_sha256 != request_sha256:
                    raise IdempotencyKeyReused(
                        "idempotency key is already bound to another request"
                    )
                replay = _result_from_idempotency(existing)
                if replay is not None:
                    return replay
                if existing.lease_until is not None and existing.lease_until > now:
                    raise IdempotencyInProgress("an earlier completion is still in progress")

            reservation = await uow.uploads.reserve_idempotency(
                case_id=intent.case_id,
                actor_id=actor.actor_id,
                operation=_IDEMPOTENCY_OPERATION,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                lease_until=now + _IDEMPOTENCY_LEASE,
                lease_owner=lease_owner,
            )
            if reservation.request_sha256 != request_sha256:
                raise IdempotencyKeyReused("idempotency key is already bound to another request")
            if reservation.lease_owner != lease_owner:
                raise IdempotencyInProgress("an earlier completion is still in progress")
            replay = _result_from_idempotency(reservation)
            if replay is not None:
                return replay
            if intent.consumed_at is not None:
                raise UploadCompletionError("upload intent was already consumed")
            if intent.expires_at <= now:
                raise UploadIntentExpired("upload intent has expired")

        try:
            metadata = await self._storage.head_object(
                bucket_id=intent.bucket_id,
                object_key=intent.object_key,
            )
        except StorageObjectNotFound as exc:
            raise UploadCompletionError("uploaded object was not found") from exc
        if metadata.bucket_id != intent.bucket_id:
            raise UploadCompletionError("object bucket does not match upload intent")
        if metadata.object_key != intent.object_key:
            raise UploadCompletionError("object key does not match upload intent")
        if metadata.size_bytes != intent.declared_size_bytes:
            raise UploadCompletionError("object size does not match upload intent")
        if (
            metadata.content_type
            and metadata.content_type.lower() != intent.accepted_content_type.lower()
        ):
            raise UploadCompletionError("object content type does not match upload intent")

        content_sha256 = await _sha256_stream(self._storage, intent)
        if metadata.sha256 and not _SHA256_RE.fullmatch(metadata.sha256):
            raise UploadCompletionError("provider checksum is invalid")
        if metadata.sha256 and metadata.sha256 != content_sha256:
            raise UploadCompletionError("provider checksum does not match object bytes")
        immutable_bucket = "creditops-originals"
        immutable_key = f"originals/{intent.case_id}/{intent.id}"
        await self._storage.copy_immutable(
            source_bucket=intent.bucket_id,
            source_key=intent.object_key,
            destination_bucket=immutable_bucket,
            destination_key=immutable_key,
            content_type=intent.accepted_content_type,
            size_bytes=intent.declared_size_bytes,
            content_sha256=content_sha256,
        )

        # Phase 2 locks the intent/idempotency rows again and performs all
        # durable registration effects atomically.  The immutable copy is
        # intentionally outside this transaction; a successful copy with a
        # failed commit is an explicit reconciliation item, never registration.
        async with self._uow_factory(actor) as uow:
            fresh_intent = await uow.uploads.get_intent(intent_id, actor.actor_id)
            if fresh_intent is None:
                raise UploadIntentNotFound("upload intent was not found")
            if fresh_intent.consumed_at is not None:
                raise UploadCompletionError("upload intent was already consumed")
            if fresh_intent.expires_at <= self._now():
                raise UploadIntentExpired("upload intent has expired")
            existing = await uow.uploads.find_idempotency(
                actor_id=actor.actor_id,
                operation=_IDEMPOTENCY_OPERATION,
                idempotency_key=idempotency_key,
            )
            if existing is None or existing.request_sha256 != request_sha256:
                raise IdempotencyKeyReused("idempotency reservation is no longer valid")
            replay = _result_from_idempotency(existing)
            if replay is not None:
                return replay
            if (
                existing.lease_owner != lease_owner
                or existing.lease_until is None
                or existing.lease_until <= self._now()
            ):
                raise IdempotencyInProgress("idempotency reservation is no longer owned")
            duplicate_id = await uow.uploads.find_duplicate_document(
                case_id=fresh_intent.case_id,
                content_sha256=content_sha256,
            )
            if duplicate_id is not None:
                duplicate_result = DuplicateUpload("DUPLICATE", duplicate_id)
                if existing.id is None:
                    raise UploadCompletionError("idempotency record has no stable id")
                await uow.uploads.complete_idempotency(
                    actor_id=actor.actor_id,
                    operation=_IDEMPOTENCY_OPERATION,
                    idempotency_key=idempotency_key,
                    response_status=200,
                    response_data={
                        "outcome": "DUPLICATE",
                        "duplicateOfDocumentId": str(duplicate_id),
                    },
                    lease_owner=lease_owner,
                )
                await uow.uploads.consume_intent(
                    intent_id=fresh_intent.id,
                    actor_id=actor.actor_id,
                    consumed_at=self._now(),
                    completion_idempotency_record_id=existing.id,
                )
                await uow.audit.append(
                    AuditEvent(
                        case_id=fresh_intent.case_id,
                        case_version=fresh_intent.case_version,
                        event_type="UPLOAD_DUPLICATE_DETECTED",
                        actor_id=actor.actor_id,
                        artifact_type="UPLOAD_INTENT",
                        artifact_id=fresh_intent.id,
                        event_data={
                            "duplicateOfDocumentId": str(duplicate_id),
                            "contentSha256": content_sha256,
                        },
                        request_id=actor.request_id,
                    )
                )
                return duplicate_result
            registration = await uow.uploads.register_verified_upload(
                intent=fresh_intent,
                immutable_bucket=immutable_bucket,
                immutable_key=immutable_key,
                detected_content_type=metadata.content_type or fresh_intent.accepted_content_type,
                content_sha256=content_sha256,
                task_input={
                    "storageBucket": immutable_bucket,
                    "storageObjectKey": immutable_key,
                    "contentSha256": content_sha256,
                },
                actor_id=actor.actor_id,
            )
            if existing.id is None:
                raise UploadCompletionError("idempotency record has no stable id")
            registered_result = RegisteredUpload(
                outcome="REGISTERED",
                document_id=registration.document_id,
                document_version_id=registration.document_version_id,
                task_id=registration.task_id,
                task_status=registration.task_status,
            )
            await uow.uploads.complete_idempotency(
                actor_id=actor.actor_id,
                operation=_IDEMPOTENCY_OPERATION,
                idempotency_key=idempotency_key,
                response_status=202,
                response_data={
                    "outcome": "REGISTERED",
                    "documentId": str(registered_result.document_id),
                    "documentVersionId": str(registered_result.document_version_id),
                    "task": {
                        "id": str(registered_result.task_id),
                        "status": registered_result.task_status,
                    },
                },
                lease_owner=lease_owner,
            )
            await uow.uploads.consume_intent(
                intent_id=fresh_intent.id,
                actor_id=actor.actor_id,
                consumed_at=self._now(),
                completion_idempotency_record_id=existing.id,
            )
            await uow.audit.append(
                AuditEvent(
                    case_id=fresh_intent.case_id,
                    case_version=fresh_intent.case_version,
                    event_type="UPLOAD_REGISTERED",
                    actor_id=actor.actor_id,
                    artifact_type="DOCUMENT_VERSION",
                    artifact_id=registered_result.document_version_id,
                    event_data={
                        "documentId": str(registered_result.document_id),
                        "contentSha256": content_sha256,
                        "taskId": str(registered_result.task_id),
                    },
                    request_id=actor.request_id,
                )
            )
            return registered_result
