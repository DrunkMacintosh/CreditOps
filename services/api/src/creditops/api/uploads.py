from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.ports.queue import QueueError, QueuePort, TaskRepository
from creditops.application.ports.storage import StorageError, StoragePort
from creditops.application.unit_of_work import ActorContext, UnitOfWorkFactory
from creditops.application.use_cases.complete_upload_intent import (
    CompleteUploadIntent,
    DuplicateUpload,
    IdempotencyInProgress,
    IdempotencyKeyReused,
    RegisteredUpload,
    UploadCompletionError,
    UploadIntentExpired,
    UploadIntentNotFound,
)
from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE
from creditops.application.use_cases.create_upload_intent import (
    CreateUploadIntent,
    CreateUploadIntentCommand,
    UploadIntentValidationError,
)
from creditops.application.use_cases.enqueue_task import EnqueueTask, TaskEnqueueError
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.observability import log_event

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["uploads"])


class CreateUploadIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    file_name: str = Field(alias="fileName", min_length=1, max_length=255)
    content_type: str = Field(alias="contentType", min_length=1, max_length=200)
    size_bytes: int = Field(alias="sizeBytes", gt=0, le=100 * 1024 * 1024)


class SignedUploadIntentResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    mode: Literal["SIGNED"]
    intent_id: UUID = Field(serialization_alias="intentId")
    expires_at: datetime = Field(serialization_alias="expiresAt")
    upload_url: str = Field(serialization_alias="uploadUrl")
    headers: dict[str, str]
    method: Literal["POST", "PUT"]


class ResumableUploadIntentResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    mode: Literal["RESUMABLE"]
    intent_id: UUID = Field(serialization_alias="intentId")
    expires_at: datetime = Field(serialization_alias="expiresAt")
    upload_url: str = Field(serialization_alias="uploadUrl")
    headers: dict[str, str]


class CompleteUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    status: Literal["PENDING"]


class DuplicateUploadResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    outcome: Literal["DUPLICATE"]
    duplicate_of_document_id: UUID = Field(serialization_alias="duplicateOfDocumentId")


class RegisteredUploadResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    outcome: Literal["REGISTERED"]
    document_id: UUID = Field(serialization_alias="documentId")
    document_version_id: UUID = Field(serialization_alias="documentVersionId")
    task: TaskStatusResponse


Actor = Annotated[ActorContext, Depends(require_actor)]


def _uow_factory(request: Request) -> UnitOfWorkFactory:
    factory = getattr(request.app.state, "uow_factory", None)
    if factory is None:
        raise ApiException(
            status_code=503,
            code="UPLOAD_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ tải tài liệu chưa sẵn sàng.",
            retryable=True,
        )
    return cast(UnitOfWorkFactory, factory)


def _storage(request: Request) -> StoragePort:
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        raise ApiException(
            status_code=503,
            code="STORAGE_SERVICE_UNAVAILABLE",
            message_vi="Kho tài liệu riêng tư chưa sẵn sàng.",
            retryable=True,
        )
    return cast(StoragePort, storage)


async def _enqueue_registered_task(request: Request, result: RegisteredUpload) -> None:
    """Enqueue only after the registration transaction has committed.

    Completion is idempotent, so a client retry can safely repair a transient
    queue outage without creating another document or task row.
    """
    queue = getattr(request.app.state, "task_queue", None)
    tasks = getattr(request.app.state, "task_repository", None)
    if queue is None or tasks is None:
        # A registered task that is never published sits PENDING forever with
        # no queue message.  This must be observable, not silent.
        log_event(
            _logger,
            logging.ERROR,
            "Registered upload was not enqueued: task queue is not configured",
            {
                "event": "upload_enqueue_skipped_no_queue",
                "taskId": str(result.task_id),
                "queueConfigured": queue is not None,
                "taskRepositoryConfigured": tasks is not None,
            },
        )
        return
    task_repository = cast(TaskRepository, tasks)
    task = await task_repository.get(result.task_id)
    if task is None:
        raise TaskEnqueueError("registered task is not visible to the queue publisher")
    envelope = TaskEnvelopeV1(
        task_id=task.id,
        case_id=task.case_id,
        case_version=task.case_version,
        document_version_id=task.document_version_id,
    )
    enqueued = await EnqueueTask(task_repository, cast(QueuePort, queue)).execute(envelope)
    log_event(
        _logger,
        logging.INFO,
        "Registered upload enqueued for document ingestion",
        {
            "event": "upload_task_enqueued",
            "taskId": str(task.id),
            "messageId": enqueued.message_id,
        },
    )


def _require_intake(actor: ActorContext) -> None:
    if INTAKE_OFFICER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tiếp nhận được yêu cầu.",
        )


@router.post(
    "/api/v1/cases/{case_id}/upload-intents",
    response_model=SignedUploadIntentResponse | ResumableUploadIntentResponse,
    status_code=201,
)
async def create_upload_intent(
    case_id: UUID,
    body: CreateUploadIntentRequest,
    actor: Actor,
    request: Request,
) -> SignedUploadIntentResponse | ResumableUploadIntentResponse:
    _require_intake(actor)
    try:
        result = await CreateUploadIntent(_uow_factory(request), _storage(request)).execute(
            actor,
            case_id,
            CreateUploadIntentCommand(
                file_name=body.file_name,
                content_type=body.content_type,
                size_bytes=body.size_bytes,
            ),
        )
    except UploadIntentValidationError as exc:
        raise ApiException(
            status_code=422,
            code="UPLOAD_INTENT_INVALID",
            message_vi="Thông tin tài liệu chưa hợp lệ.",
        ) from exc
    except StorageError as exc:
        raise ApiException(
            status_code=502,
            code="STORAGE_AUTHORIZATION_FAILED",
            message_vi="Không thể cấp quyền tải tài liệu.",
            retryable=True,
        ) from exc
    authorization = result.authorization
    if authorization.mode == "SIGNED":
        if authorization.method is None:
            raise ApiException(
                status_code=502,
                code="STORAGE_AUTHORIZATION_INVALID",
                message_vi="Quyền tải tài liệu không hợp lệ.",
                retryable=True,
            )
        return SignedUploadIntentResponse(
            mode="SIGNED",
            intent_id=result.intent.id,
            expires_at=result.intent.expires_at,
            upload_url=authorization.upload_url,
            headers=dict(authorization.headers),
            method=authorization.method,
        )
    return ResumableUploadIntentResponse(
        mode="RESUMABLE",
        intent_id=result.intent.id,
        expires_at=result.intent.expires_at,
        upload_url=authorization.upload_url,
        headers=dict(authorization.headers),
    )


@router.post(
    "/api/v1/upload-intents/{intent_id}/complete",
    response_model=DuplicateUploadResponse | RegisteredUploadResponse,
)
async def complete_upload_intent(
    intent_id: UUID,
    body: CompleteUploadRequest,
    actor: Actor,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> DuplicateUploadResponse | RegisteredUploadResponse:
    del body
    _require_intake(actor)
    try:
        result = await CompleteUploadIntent(_uow_factory(request), _storage(request)).execute(
            actor,
            intent_id,
            idempotency_key,
        )
    except UploadIntentNotFound as exc:
        raise ApiException(
            status_code=404,
            code="UPLOAD_INTENT_NOT_FOUND",
            message_vi="Không tìm thấy phiên tải tài liệu.",
        ) from exc
    except UploadIntentExpired as exc:
        raise ApiException(
            status_code=410,
            code="UPLOAD_INTENT_EXPIRED",
            message_vi="Phiên tải tài liệu đã hết hạn.",
        ) from exc
    except IdempotencyKeyReused as exc:
        raise ApiException(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message_vi="Khóa idempotency đã được dùng cho yêu cầu khác.",
        ) from exc
    except IdempotencyInProgress as exc:
        raise ApiException(
            status_code=409,
            code="UPLOAD_COMPLETION_IN_PROGRESS",
            message_vi="Yêu cầu hoàn tất tài liệu trước đó vẫn đang được xử lý.",
            retryable=True,
        ) from exc
    except UploadCompletionError as exc:
        raise ApiException(
            status_code=422,
            code="UPLOAD_VERIFICATION_FAILED",
            message_vi="Không thể xác minh tài liệu đã tải lên.",
        ) from exc
    except StorageError as exc:
        raise ApiException(
            status_code=502,
            code="STORAGE_VERIFICATION_FAILED",
            message_vi="Không thể xác minh tài liệu trong kho riêng tư.",
            retryable=True,
        ) from exc
    if isinstance(result, DuplicateUpload):
        return DuplicateUploadResponse(
            outcome="DUPLICATE",
            duplicate_of_document_id=result.duplicate_of_document_id,
        )
    if isinstance(result, RegisteredUpload):
        try:
            await _enqueue_registered_task(request, result)
        except (QueueError, TaskEnqueueError) as exc:
            raise ApiException(
                status_code=503,
                code="TASK_QUEUE_UNAVAILABLE",
                message_vi="Tài liệu đã đăng ký nhưng chưa thể đưa vào hàng đợi xử lý.",
                retryable=True,
            ) from exc
        return RegisteredUploadResponse(
            outcome="REGISTERED",
            document_id=result.document_id,
            document_version_id=result.document_version_id,
            task=TaskStatusResponse(id=result.task_id, status="PENDING"),
        )
    raise ApiException(
        status_code=500,
        code="UPLOAD_RESULT_INVALID",
        message_vi="Kết quả đăng ký tài liệu không hợp lệ.",
    )
