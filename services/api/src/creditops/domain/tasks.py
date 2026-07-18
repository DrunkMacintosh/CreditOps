from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId, DocumentVersionId, TaskId
from creditops.domain.orchestration import TaskType


class TaskEnvelopeV1(BaseModel):
    """Identifier-only queue envelope for a durable task.

    Extended backward-compatibly: ``task_type`` defaults to
    ``DOCUMENT_INGESTION`` so any message written before agent tasks existed
    still validates and remains processable, and ``document_version_id`` is now
    optional because agent tasks (orchestration and specialist roles) are
    case-scoped and carry no document identifier.  The envelope still contains
    only durable identifiers — never a document body, extracted data, or secret.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    task_id: TaskId
    case_id: CaseId
    case_version: int = Field(ge=1)
    task_type: TaskType = TaskType.DOCUMENT_INGESTION
    document_version_id: DocumentVersionId | None = None

    @model_validator(mode="after")
    def _document_scope_matches_type(self) -> Self:
        # A document-ingestion task is always bound to a document version; an
        # agent task never is.  This mirrors the processing_tasks document-scope
        # check constraint, so a tampered queue message cannot smuggle a
        # mis-scoped task past the worker's claim.
        is_ingestion = self.task_type is TaskType.DOCUMENT_INGESTION
        if is_ingestion != (self.document_version_id is not None):
            raise ValueError(
                "document ingestion requires a document version id; "
                "agent tasks must not carry one"
            )
        return self
