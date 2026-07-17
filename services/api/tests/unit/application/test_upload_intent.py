from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.repositories import (
    AuditEvent,
    CaseRecord,
    IdempotencyRecord,
    UploadIntentRecord,
    UploadRegistration,
)
from creditops.application.ports.storage import (
    StorageObjectMetadata,
    UploadAuthorization,
)
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.complete_upload_intent import (
    CompleteUploadIntent,
    IdempotencyInProgress,
    RegisteredUpload,
    UploadCompletionError,
    UploadIntentExpired,
    _request_hash,
)
from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE
from creditops.application.use_cases.create_upload_intent import (
    CreateUploadIntent,
    CreateUploadIntentCommand,
    UploadIntentValidationError,
)

CASE_ID = UUID("10000000-0000-0000-0000-000000000001")
OFFICER_A = UUID("20000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 7, 18, 4, 0, tzinfo=UTC)


class FakeStorage:
    def __init__(self) -> None:
        self.mode = "SIGNED"
        self.metadata = StorageObjectMetadata(
            bucket_id="creditops-incoming",
            object_key="",
            size_bytes=100,
            content_type="application/pdf",
        )
        self.chunks: list[bytes] = [b"a" * 100]
        self.authorizations = 0
        self.copies: list[tuple[str, str, str, str]] = []

    async def create_upload_authorization(self, **kwargs: object) -> UploadAuthorization:
        self.authorizations += 1
        expires_at = kwargs["expires_at"]
        assert isinstance(expires_at, datetime)
        if self.mode == "RESUMABLE":
            return UploadAuthorization(
                mode="RESUMABLE",
                upload_url="https://storage.test/upload/resumable",
                expires_at=expires_at,
                headers={"x-signature": "signed", "x-upsert": "false"},
            )
        return UploadAuthorization(
            mode="SIGNED",
            upload_url="https://storage.test/upload/signed",
            expires_at=expires_at,
            method="PUT",
            headers={"Content-Type": "application/pdf", "x-upsert": "false"},
        )

    async def head_object(self, *, bucket_id: str, object_key: str) -> StorageObjectMetadata:
        del bucket_id, object_key
        return self.metadata

    async def _open(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    def open_object(self, *, bucket_id: str, object_key: str) -> AsyncIterator[bytes]:
        del bucket_id, object_key
        return self._open()

    async def copy_immutable(self, **kwargs: str) -> None:
        self.copies.append(
            (
                kwargs["source_bucket"],
                kwargs["source_key"],
                kwargs["destination_bucket"],
                kwargs["destination_key"],
            )
        )


class FakeUploads:
    def __init__(self) -> None:
        self.intents: dict[UUID, UploadIntentRecord] = {}
        self.idempotency: dict[tuple[UUID, str, str], IdempotencyRecord] = {}
        self.duplicates: dict[str, UUID] = {}
        self.registrations: list[UploadRegistration] = []

    async def create_intent(self, **kwargs: object) -> UploadIntentRecord:
        case = kwargs["case"]
        assert isinstance(case, CaseRecord)
        intent = UploadIntentRecord(
            id=kwargs["intent_id"],
            case_id=case.id,
            case_version=case.version,
            assigned_officer_id=case.assigned_officer_id,
            bucket_id=kwargs["bucket_id"],
            object_key=kwargs["object_key"],
            original_filename=kwargs["original_filename"],
            accepted_content_type=kwargs["content_type"],
            declared_size_bytes=kwargs["declared_size_bytes"],
            expires_at=kwargs["expires_at"],
        )
        self.intents[intent.id] = intent
        return intent

    async def get_intent(self, intent_id: UUID, actor_id: UUID) -> UploadIntentRecord | None:
        intent = self.intents.get(intent_id)
        return intent if intent and intent.assigned_officer_id == actor_id else None

    async def find_idempotency(self, **kwargs: object) -> IdempotencyRecord | None:
        return self.idempotency.get(
            (kwargs["actor_id"], kwargs["operation"], kwargs["idempotency_key"])
        )

    async def reserve_idempotency(self, **kwargs: object) -> IdempotencyRecord:
        key = (kwargs["actor_id"], kwargs["operation"], kwargs["idempotency_key"])
        old = self.idempotency.get(key)
        if old is not None and old.lease_until and old.lease_until > NOW:
            return old
        record = IdempotencyRecord(
            id=uuid4(),
            actor_id=kwargs["actor_id"],
            operation=kwargs["operation"],
            idempotency_key=kwargs["idempotency_key"],
            request_sha256=kwargs["request_sha256"],
            case_id=kwargs["case_id"],
            lease_owner=kwargs["lease_owner"],
            lease_until=kwargs["lease_until"],
        )
        self.idempotency[key] = record
        return record

    async def complete_idempotency(self, **kwargs: object) -> None:
        key = (kwargs["actor_id"], kwargs["operation"], kwargs["idempotency_key"])
        old = self.idempotency[key]
        self.idempotency[key] = IdempotencyRecord(
            actor_id=old.actor_id,
            operation=old.operation,
            idempotency_key=old.idempotency_key,
            request_sha256=old.request_sha256,
            case_id=old.case_id,
            id=old.id,
            lease_owner=None,
            response_status=kwargs["response_status"],
            response_data=kwargs["response_data"],
            completed_at=NOW,
        )

    async def find_duplicate_document(self, **kwargs: object) -> UUID | None:
        return self.duplicates.get(kwargs["content_sha256"])

    async def register_verified_upload(self, **kwargs: object) -> UploadRegistration:
        registration = UploadRegistration(uuid4(), uuid4(), uuid4(), "PENDING")
        self.registrations.append(registration)
        return registration

    async def consume_intent(self, **kwargs: object) -> None:
        assert kwargs["completion_idempotency_record_id"] is not None
        old = self.intents[kwargs["intent_id"]]
        self.intents[old.id] = replace(old, consumed_at=kwargs["consumed_at"])


class FakeCases:
    async def require_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord:
        assert case_id == CASE_ID and actor_id == OFFICER_A
        return CaseRecord(CASE_ID, 1, OFFICER_A, "1", "Vốn lưu động", NOW)


class FakeAudit:
    async def append(self, event: AuditEvent) -> None:
        del event


class FakeUow:
    def __init__(self, uploads: FakeUploads) -> None:
        self.cases = FakeCases()
        self.uploads = uploads
        self.audit = FakeAudit()

    async def __aenter__(self) -> FakeUow:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class FakeUowFactory:
    def __init__(self, uploads: FakeUploads) -> None:
        self.uploads = uploads

    def __call__(self, actor: ActorContext) -> FakeUow:
        del actor
        return FakeUow(self.uploads)


def actor() -> ActorContext:
    return ActorContext(OFFICER_A, frozenset({INTAKE_OFFICER_ROLE}), "req-1")


@pytest.mark.asyncio
async def test_create_intent_uses_exact_path_and_signed_mode() -> None:
    uploads = FakeUploads()
    storage = FakeStorage()
    result = await CreateUploadIntent(
        FakeUowFactory(uploads),
        storage,
        now=lambda: NOW,
        id_factory=lambda: UUID("30000000-0000-0000-0000-000000000001"),
    ).execute(actor(), CASE_ID, CreateUploadIntentCommand("scan.pdf", "application/pdf", 100))

    assert (
        result.intent.object_key
        == "incoming/10000000-0000-0000-0000-000000000001/30000000-0000-0000-0000-000000000001"
    )
    assert result.authorization.mode == "SIGNED"
    assert result.authorization.headers["x-upsert"] == "false"


@pytest.mark.asyncio
async def test_create_intent_uses_resumable_mode_at_six_mib() -> None:
    uploads = FakeUploads()
    storage = FakeStorage()
    storage.mode = "RESUMABLE"
    result = await CreateUploadIntent(
        FakeUowFactory(uploads),
        storage,
        now=lambda: NOW,
    ).execute(
        actor(), CASE_ID, CreateUploadIntentCommand("scan.pdf", "application/pdf", 6 * 1024 * 1024)
    )
    assert result.authorization.mode == "RESUMABLE"
    assert "x-signature" in result.authorization.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "content_type"),
    [
        ("../scan.pdf", "application/pdf"),
        ("scan.pdf", "image/png"),
        ("scan\n.pdf", "application/pdf"),
    ],
)
async def test_create_rejects_unsafe_or_mismatched_filename(name: str, content_type: str) -> None:
    with pytest.raises(UploadIntentValidationError):
        await CreateUploadIntent(
            FakeUowFactory(FakeUploads()),
            FakeStorage(),
            now=lambda: NOW,
        ).execute(actor(), CASE_ID, CreateUploadIntentCommand(name, content_type, 100))


@pytest.mark.asyncio
async def test_complete_rejects_expiry_and_wrong_object_path() -> None:
    uploads = FakeUploads()
    storage = FakeStorage()
    intent = await CreateUploadIntent(FakeUowFactory(uploads), storage, now=lambda: NOW).execute(
        actor(), CASE_ID, CreateUploadIntentCommand("scan.pdf", "application/pdf", 100)
    )
    storage.metadata = StorageObjectMetadata(
        "creditops-incoming", "another-case/scan.pdf", 100, "application/pdf"
    )
    with pytest.raises(UploadCompletionError, match="key"):
        await CompleteUploadIntent(FakeUowFactory(uploads), storage, now=lambda: NOW).execute(
            actor(), intent.intent.id, "same-idempotency-key"
        )
    uploads.intents[intent.intent.id] = replace(
        intent.intent, expires_at=NOW - timedelta(seconds=1)
    )
    with pytest.raises(UploadIntentExpired):
        await CompleteUploadIntent(FakeUowFactory(uploads), storage, now=lambda: NOW).execute(
            actor(), intent.intent.id, "another-idempotency-key"
        )


@pytest.mark.asyncio
async def test_complete_registers_pending_task_and_replays_stably() -> None:
    uploads = FakeUploads()
    storage = FakeStorage()
    created = await CreateUploadIntent(FakeUowFactory(uploads), storage, now=lambda: NOW).execute(
        actor(), CASE_ID, CreateUploadIntentCommand("scan.pdf", "application/pdf", 100)
    )
    storage.metadata = StorageObjectMetadata(
        "creditops-incoming", created.intent.object_key, 100, "application/pdf"
    )
    service = CompleteUploadIntent(FakeUowFactory(uploads), storage, now=lambda: NOW)
    first = await service.execute(actor(), created.intent.id, "stable-idempotency-key")
    second = await service.execute(actor(), created.intent.id, "stable-idempotency-key")
    assert isinstance(first, RegisteredUpload)
    assert second == first
    assert len(uploads.registrations) == 1
    assert storage.copies and storage.copies[0][2] == "creditops-originals"


@pytest.mark.asyncio
async def test_active_idempotency_lease_is_not_replayed_or_reclaimed() -> None:
    uploads = FakeUploads()
    storage = FakeStorage()
    created = await CreateUploadIntent(FakeUowFactory(uploads), storage, now=lambda: NOW).execute(
        actor(), CASE_ID, CreateUploadIntentCommand("scan.pdf", "application/pdf", 100)
    )
    await uploads.reserve_idempotency(
        case_id=CASE_ID,
        actor_id=OFFICER_A,
        operation="COMPLETE_UPLOAD_INTENT_V1",
        idempotency_key="busy-idempotency-key",
        request_sha256=_request_hash(created.intent.id),
        lease_until=NOW + timedelta(minutes=1),
        lease_owner=OFFICER_A,
    )
    with pytest.raises(IdempotencyInProgress):
        await CompleteUploadIntent(FakeUowFactory(uploads), storage, now=lambda: NOW).execute(
            actor(), created.intent.id, "busy-idempotency-key"
        )
