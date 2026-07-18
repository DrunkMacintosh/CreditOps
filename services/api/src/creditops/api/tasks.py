from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord, TaskRepository
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class CheckpointResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    sequence_no: int = Field(serialization_alias="sequenceNo")
    checkpoint_type: str = Field(serialization_alias="checkpointType")
    checkpoint_schema_version: str = Field(serialization_alias="checkpointSchemaVersion")
    created_at: datetime = Field(serialization_alias="createdAt")


class TaskResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    task_type: str = Field(serialization_alias="taskType")
    document_version_id: UUID | None = Field(serialization_alias="documentVersionId")
    status: str
    attempt_count: int = Field(serialization_alias="attemptCount")
    max_attempts: int = Field(serialization_alias="maxAttempts")
    available_at: datetime = Field(serialization_alias="availableAt")
    checkpoint: CheckpointResponse | None


Actor = Annotated[ActorContext, Depends(require_actor)]


def _tasks(request: Request) -> TaskRepository:
    repository = getattr(request.app.state, "task_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="TASK_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ xử lý hồ sơ chưa sẵn sàng.",
            retryable=True,
        )
    return cast(TaskRepository, repository)


def _checkpoint(checkpoint: TaskCheckpoint | None) -> CheckpointResponse | None:
    if checkpoint is None:
        return None
    return CheckpointResponse(
        sequence_no=checkpoint.sequence_no,
        checkpoint_type=checkpoint.checkpoint_type,
        checkpoint_schema_version=checkpoint.checkpoint_schema_version,
        created_at=checkpoint.created_at,
    )


def _response(task: TaskRecord, checkpoint: TaskCheckpoint | None) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        case_id=task.case_id,
        case_version=task.case_version,
        task_type=task.task_type.value,
        document_version_id=task.document_version_id,
        status=task.status.value,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        available_at=task.available_at,
        checkpoint=_checkpoint(checkpoint),
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, actor: Actor, request: Request) -> TaskResponse:
    if INTAKE_OFFICER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tiếp nhận được yêu cầu.",
        )
    repository = _tasks(request)
    # The task repository must apply the assignment filter before returning a
    # row.  The API deliberately does not expose input payloads or lease data.
    task = await repository.get(task_id, actor_id=actor.actor_id)
    if task is None:
        raise ApiException(
            status_code=404,
            code="TASK_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy tác vụ hoặc bạn không có quyền truy cập.",
        )
    checkpoint = await repository.latest_checkpoint(
        task_id=task.id,
        case_id=task.case_id,
        case_version=task.case_version,
        document_version_id=task.document_version_id,
    )
    return _response(task, checkpoint)
