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


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...
