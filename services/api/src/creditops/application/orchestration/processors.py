"""Per-task-type worker processors and the dispatch registry.

DOCUMENT_INGESTION keeps its existing pipeline processor unchanged.
ORCHESTRATOR_PLAN runs the advance/planner logic with checkpoints
(plan built -> tasks created -> enqueued) so a redelivery resumes from the
latest checkpoint.  Any task type with no registered processor resolves to a
manual-review processor: it fails closed (FAILED_MANUAL_REVIEW) with an audit
event instead of crashing the worker.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from creditops.application.orchestration.advance import AdvanceCase
from creditops.application.orchestration.roles import CASE_ORCHESTRATOR_ROLE
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.use_cases.run_worker_once import (
    CheckpointCallback,
    StageResult,
    TaskProcessor,
    WorkerOutcome,
)
from creditops.domain.orchestration import TaskType

_ENQUEUED_CHECKPOINT = "ENQUEUED"


class OrchestratorPlanProcessor(TaskProcessor):
    """Run one idempotent advance for the case behind an ORCHESTRATOR_PLAN task."""

    def __init__(self, advance: AdvanceCase) -> None:
        self._advance = advance

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        if checkpoint is not None and checkpoint.checkpoint_type == _ENQUEUED_CHECKPOINT:
            # A prior execution already advanced this case to completion.  Advance
            # is idempotent, so re-running would be harmless, but short-circuiting
            # on the terminal checkpoint is the explicit resume-from-checkpoint
            # path required for the orchestrator.
            return StageResult()

        result = await self._advance.execute(task.case_id)
        await save_checkpoint(
            "PLAN_BUILT",
            {
                "planSource": result.plan.source,
                "proposalStatus": result.proposal_status,
                "readyTaskTypes": [step.task_type.value for step in result.plan.steps],
            },
        )
        await save_checkpoint(
            "TASKS_CREATED",
            {"createdTaskIds": [str(task_id) for task_id in result.created_task_ids]},
        )
        await save_checkpoint(
            _ENQUEUED_CHECKPOINT,
            {
                "enqueuedTaskIds": [str(task_id) for task_id in result.enqueued_task_ids],
                "deadlock": result.deadlock is not None,
            },
        )
        return StageResult()


class ManualReviewProcessor(TaskProcessor):
    """Fail-closed processor for an unknown/unregistered task type."""

    def __init__(
        self,
        repository: OrchestrationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        execution_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._execution_id_factory = execution_id_factory or uuid4

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        del checkpoint, save_checkpoint
        await self._repository.append_audit(
            OrchestrationAuditEvent(
                case_id=task.case_id,
                case_version=task.case_version,
                event_type="ORCHESTRATION_UNKNOWN_TASK_TYPE",
                execution_id=self._execution_id_factory(),
                artifact_type="PROCESSING_TASK",
                artifact_id=task.id,
                event_data={
                    "role": CASE_ORCHESTRATOR_ROLE,
                    "recordedAt": self._clock().isoformat(),
                    "taskType": task.task_type.value,
                },
            )
        )
        return StageResult(
            WorkerOutcome.FAILED_MANUAL_REVIEW,
            f"no processor registered for task type {task.task_type.value}",
        )


class ProcessorRegistry:
    """Map task types to processors; unknown types get manual review."""

    def __init__(
        self,
        processors: Mapping[TaskType, TaskProcessor],
        *,
        fallback: TaskProcessor,
    ) -> None:
        self._processors = dict(processors)
        self._fallback = fallback

    def processor_for(self, task_type: TaskType) -> TaskProcessor:
        return self._processors.get(task_type, self._fallback)
