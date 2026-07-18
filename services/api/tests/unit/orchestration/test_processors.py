from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_advance import CASE_ID, FakeOrchestrationRepository, RecordingQueue

from creditops.application.orchestration.advance import AdvanceCase
from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.planner import OrchestrationPlanner
from creditops.application.orchestration.processors import (
    ManualReviewProcessor,
    OrchestratorPlanProcessor,
    ProcessorRegistry,
)
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.use_cases.run_worker_once import (
    StageResult,
    WorkerOutcome,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import TaskType

NOW = datetime(2026, 7, 18, 7, 0, tzinfo=UTC)
TEMPLATE = DependencyTemplate.canonical()


def plan_task(task_type: TaskType = TaskType.ORCHESTRATOR_PLAN) -> TaskRecord:
    return TaskRecord(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=1,
        document_version_id=None,
        status=TaskStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        available_at=NOW,
        lease_token=uuid4(),
        lease_until=NOW,
        input_schema_version="1",
        input_payload={},
        idempotency_key=f"ORCH-PLAN:{CASE_ID}:1",
        task_type=task_type,
    )


class CheckpointRecorder:
    def __init__(self) -> None:
        self.saved: list[TaskCheckpoint] = []

    async def save(self, checkpoint_type: str, data: dict[str, object]) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            task_id=uuid4(),
            case_id=CASE_ID,
            case_version=1,
            document_version_id=None,
            sequence_no=len(self.saved) + 1,
            checkpoint_type=checkpoint_type,
            checkpoint_schema_version="1",
            checkpoint_data=data,
            created_at=NOW,
        )
        self.saved.append(checkpoint)
        return checkpoint


def build_processor(
    repository: FakeOrchestrationRepository, queue: RecordingQueue
) -> OrchestratorPlanProcessor:
    return OrchestratorPlanProcessor(
        AdvanceCase(
            repository,
            queue,
            OrchestrationPlanner(TEMPLATE, gateway=None),
            template=TEMPLATE,
            clock=lambda: NOW,
        )
    )


@pytest.mark.asyncio
async def test_plan_processor_checkpoints_each_stage_in_order() -> None:
    repository = FakeOrchestrationRepository()
    queue = RecordingQueue()
    recorder = CheckpointRecorder()
    processor = build_processor(repository, queue)

    result = await processor.process(plan_task(), None, recorder.save)

    assert result == StageResult()
    assert [checkpoint.checkpoint_type for checkpoint in recorder.saved] == [
        "PLAN_BUILT",
        "TASKS_CREATED",
        "ENQUEUED",
    ]
    assert len(queue.sent) == 2


@pytest.mark.asyncio
async def test_plan_processor_resumes_from_the_terminal_checkpoint() -> None:
    repository = FakeOrchestrationRepository()
    queue = RecordingQueue()
    recorder = CheckpointRecorder()
    processor = build_processor(repository, queue)

    await processor.process(plan_task(), None, recorder.save)
    terminal = recorder.saved[-1]
    resumed = await processor.process(plan_task(), terminal, recorder.save)

    assert resumed == StageResult()
    # The redelivery neither re-planned nor re-enqueued anything.
    assert len(recorder.saved) == 3
    assert len(queue.sent) == 2


@pytest.mark.asyncio
async def test_redelivery_after_a_mid_run_crash_creates_no_duplicate_effects() -> None:
    repository = FakeOrchestrationRepository()
    queue = RecordingQueue()
    recorder = CheckpointRecorder()
    processor = build_processor(repository, queue)

    # First delivery checkpointed PLAN_BUILT and crashed before ENQUEUED; the
    # redelivery re-runs the idempotent advance from that checkpoint.
    await processor.process(plan_task(), None, recorder.save)
    non_terminal = recorder.saved[0]
    await processor.process(plan_task(), non_terminal, recorder.save)

    assert len(repository.tasks_by_key) == 2
    assert len(queue.sent) == 2


@pytest.mark.asyncio
async def test_unknown_task_type_goes_to_manual_review_with_an_audit_event() -> None:
    repository = FakeOrchestrationRepository()
    recorder = CheckpointRecorder()
    processor = ManualReviewProcessor(
        repository, clock=lambda: NOW, execution_id_factory=uuid4
    )
    task = plan_task(TaskType.CREDIT_UNDERWRITING)

    result = await processor.process(task, None, recorder.save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert "CREDIT_UNDERWRITING" in result.reason
    events = [
        event
        for event in repository.audit_events
        if event.event_type == "ORCHESTRATION_UNKNOWN_TASK_TYPE"
    ]
    assert len(events) == 1
    assert events[0].artifact_id == task.id
    assert events[0].event_data["role"] == "CASE_ORCHESTRATOR"


@pytest.mark.asyncio
async def test_registry_routes_by_type_and_falls_back_to_manual_review() -> None:
    repository = FakeOrchestrationRepository()
    queue = RecordingQueue()

    class SentinelIngestion:
        calls = 0

        async def process(self, task, checkpoint, save_checkpoint):  # type: ignore[no-untyped-def]
            del task, checkpoint, save_checkpoint
            SentinelIngestion.calls += 1
            return StageResult()

    fallback = ManualReviewProcessor(repository, clock=lambda: NOW)
    registry = ProcessorRegistry(
        {
            TaskType.DOCUMENT_INGESTION: SentinelIngestion(),
            TaskType.ORCHESTRATOR_PLAN: build_processor(repository, queue),
        },
        fallback=fallback,
    )

    assert isinstance(
        registry.processor_for(TaskType.DOCUMENT_INGESTION), SentinelIngestion
    )
    assert isinstance(
        registry.processor_for(TaskType.ORCHESTRATOR_PLAN), OrchestratorPlanProcessor
    )
    assert registry.processor_for(TaskType.CREDIT_OPERATIONS) is fallback
