"""Kick off orchestration by enqueuing a single ORCHESTRATOR_PLAN task.

This is the seam the intake handoff should call once a handoff row is written
(state READY_FOR_SPECIALIST_REVIEW) so the deterministic engine takes over.  No
application code writes intake handoffs yet, so the orchestration API exposes
this as an explicit trigger; the wiring point is intentionally the same use case
either caller would invoke.  The kick-off is idempotent per case version: a
duplicate call enqueues no second planning task.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from creditops.application.orchestration.roles import CASE_ORCHESTRATOR_ROLE
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.ports.queue import QueuePort
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import TaskType
from creditops.domain.tasks import TaskEnvelopeV1


class KickoffError(RuntimeError):
    """The orchestration plan task cannot be created for this case."""


class KickoffCaseNotFound(KickoffError):
    pass


@dataclass(frozen=True, slots=True)
class KickoffResult:
    task_id: UUID
    case_version: int
    status: TaskStatus
    created: bool


def _plan_idempotency_key(case_id: UUID, case_version: int) -> str:
    return f"ORCH-PLAN:{case_id}:{case_version}"


class KickoffOrchestration:
    def __init__(
        self,
        repository: OrchestrationRepository,
        queue: QueuePort,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
        execution_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4
        self._execution_id_factory = execution_id_factory or uuid4

    async def execute(self, case_id: UUID) -> KickoffResult:
        snapshot = await self._repository.load_snapshot(case_id)
        if snapshot is None:
            raise KickoffCaseNotFound("case is not visible to the orchestrator")

        result = await self._repository.create_task(
            task_id=self._id_factory(),
            case_id=case_id,
            case_version=snapshot.case_version,
            task_type=TaskType.ORCHESTRATOR_PLAN,
            idempotency_key=_plan_idempotency_key(case_id, snapshot.case_version),
            input_payload={"trigger": "orchestration.kickoff"},
        )
        if result.created:
            envelope = TaskEnvelopeV1(
                task_id=result.row.task_id,
                case_id=case_id,
                case_version=snapshot.case_version,
                task_type=TaskType.ORCHESTRATOR_PLAN,
                document_version_id=None,
            )
            await self._queue.send(envelope)
            await self._audit(
                case_id,
                snapshot.case_version,
                result.row.task_id,
                {"taskId": str(result.row.task_id)},
            )
        return KickoffResult(
            task_id=result.row.task_id,
            case_version=snapshot.case_version,
            status=result.row.status,
            created=result.created,
        )

    async def _audit(
        self,
        case_id: UUID,
        case_version: int,
        artifact_id: UUID,
        event_data: Mapping[str, object],
    ) -> None:
        await self._repository.append_audit(
            OrchestrationAuditEvent(
                case_id=case_id,
                case_version=case_version,
                event_type="ORCHESTRATION_KICKOFF",
                execution_id=self._execution_id_factory(),
                artifact_type="PROCESSING_TASK",
                artifact_id=artifact_id,
                event_data={
                    "role": CASE_ORCHESTRATOR_ROLE,
                    "recordedAt": self._clock().isoformat(),
                    **dict(event_data),
                },
            )
        )
