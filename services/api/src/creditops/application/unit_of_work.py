from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from creditops.application.ports.repositories import (
    AuditRepository,
    CaseRepository,
    UploadRepository,
)


@dataclass(frozen=True, slots=True)
class ActorContext:
    actor_id: UUID
    roles: frozenset[str]
    request_id: str


class UnitOfWork(Protocol):
    @property
    def cases(self) -> CaseRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    @property
    def uploads(self) -> UploadRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, actor: ActorContext) -> UnitOfWork: ...
