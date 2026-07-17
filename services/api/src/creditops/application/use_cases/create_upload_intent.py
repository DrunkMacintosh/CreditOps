from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from creditops.application.ports.repositories import AuditEvent, UploadIntentRecord
from creditops.application.ports.storage import StoragePort, UploadAuthorization
from creditops.application.unit_of_work import ActorContext, UnitOfWorkFactory
from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
TUS_THRESHOLD_BYTES = 6 * 1024 * 1024
UPLOAD_INTENT_TTL = timedelta(minutes=15)
SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)
_EXTENSION_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class UploadIntentValidationError(ValueError):
    """The requested upload is outside the bounded intake contract."""


@dataclass(frozen=True, slots=True)
class CreateUploadIntentCommand:
    file_name: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CreatedUploadIntent:
    intent: UploadIntentRecord
    authorization: UploadAuthorization


def sanitize_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if any(char in normalized for char in ("/", "\\")) or any(
        ord(char) < 32 or ord(char) == 127 for char in normalized
    ):
        raise UploadIntentValidationError("filename contains a path separator or control character")
    normalized = normalized.strip(" .")
    if not normalized:
        raise UploadIntentValidationError("filename is empty")
    if len(normalized) > 255:
        normalized = normalized[:255].rstrip(" .")
    if not normalized:
        raise UploadIntentValidationError("filename is empty")
    return normalized


class CreateUploadIntent:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: StoragePort,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    async def execute(
        self,
        actor: ActorContext,
        case_id: UUID,
        command: CreateUploadIntentCommand,
    ) -> CreatedUploadIntent:
        if INTAKE_OFFICER_ROLE not in actor.roles:
            raise UploadIntentValidationError("intake officer role is required")
        filename = sanitize_filename(command.file_name)
        content_type = command.content_type.strip().lower()
        if content_type not in SUPPORTED_CONTENT_TYPES:
            raise UploadIntentValidationError("content type is not supported")
        extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if _EXTENSION_CONTENT_TYPES.get(extension) != content_type:
            raise UploadIntentValidationError("filename extension does not match content type")
        if command.size_bytes <= 0 or command.size_bytes > MAX_UPLOAD_BYTES:
            raise UploadIntentValidationError("size is outside the upload limit")

        now = self._now()
        expires_at = now + UPLOAD_INTENT_TTL
        intent_id = self._id_factory()
        object_key = f"incoming/{case_id}/{intent_id}"
        async with self._uow_factory(actor) as uow:
            case = await uow.cases.require_assigned(case_id, actor.actor_id)
            authorization = await self._storage.create_upload_authorization(
                bucket_id="creditops-incoming",
                object_key=object_key,
                content_type=content_type,
                size_bytes=command.size_bytes,
                expires_at=expires_at,
            )
            intent = await uow.uploads.create_intent(
                intent_id=intent_id,
                case=case,
                original_filename=filename,
                content_type=content_type,
                declared_size_bytes=command.size_bytes,
                bucket_id="creditops-incoming",
                object_key=object_key,
                expires_at=expires_at,
            )
            await uow.audit.append(
                AuditEvent(
                    case_id=case.id,
                    case_version=case.version,
                    event_type="UPLOAD_INTENT_CREATED",
                    actor_id=actor.actor_id,
                    artifact_type="UPLOAD_INTENT",
                    artifact_id=intent.id,
                    event_data={
                        "fileName": intent.original_filename,
                        "contentType": intent.accepted_content_type,
                        "sizeBytes": intent.declared_size_bytes,
                        "mode": authorization.mode,
                    },
                    request_id=actor.request_id,
                )
            )
            return CreatedUploadIntent(intent=intent, authorization=authorization)
