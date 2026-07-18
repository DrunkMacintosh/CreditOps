"""The idempotent 'advance case' use case — the heart of the orchestrator.

Given a case it: derives and persists human gates, evaluates deterministic
readiness, builds a plan (default or validated LLM proposal), ensures the next
tasks exist and are enqueued, records planner-proposal history, and appends
agent audit events.  Every write is idempotent (unique idempotency keys and
gate keys), so a duplicate delivery or a duplicate advance produces no duplicate
tasks or effects.  The orchestrator never writes a fact, finding, or conclusion
and never resolves a gap, conflict, or challenge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from creditops.application.orchestration.gates import derive_effective_gates
from creditops.application.orchestration.graph import (
    Deadlock,
    DependencyTemplate,
    detect_deadlock,
)
from creditops.application.orchestration.planner import (
    OrchestrationPlanner,
    Plan,
    ProposalOutcome,
)
from creditops.application.orchestration.readiness import ReadinessReport, evaluate_readiness
from creditops.application.orchestration.roles import CASE_ORCHESTRATOR_ROLE
from creditops.application.ports.orchestration import (
    GateRecord,
    OrchestrationAuditEvent,
    OrchestrationRepository,
    OrchestrationSnapshot,
)
from creditops.application.ports.queue import QueuePort
from creditops.domain.orchestration import TaskType
from creditops.domain.tasks import TaskEnvelopeV1


class OrchestrationError(RuntimeError):
    """The orchestrator cannot advance the case from its current state."""


class CaseNotFound(OrchestrationError):
    pass


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    execution_id: UUID
    case_id: UUID
    case_version: int
    recorded_at: datetime
    plan: Plan
    proposal_status: str
    validation_errors: tuple[str, ...]
    readiness: ReadinessReport
    gates: tuple[GateRecord, ...]
    created_task_ids: tuple[UUID, ...]
    enqueued_task_ids: tuple[UUID, ...]
    superseded_task_ids: tuple[str, ...]
    deadlock: Deadlock | None


def _idempotency_key(case_id: UUID, case_version: int, task_type: TaskType) -> str:
    return f"ORCH:{case_id}:{case_version}:{task_type.value}"


class AdvanceCase:
    def __init__(
        self,
        repository: OrchestrationRepository,
        queue: QueuePort,
        planner: OrchestrationPlanner,
        *,
        template: DependencyTemplate | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
        execution_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._planner = planner
        self._template = template or DependencyTemplate.canonical()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4
        self._execution_id_factory = execution_id_factory or uuid4

    async def execute(self, case_id: UUID) -> AdvanceResult:
        snapshot = await self._repository.load_snapshot(case_id)
        if snapshot is None:
            raise CaseNotFound("case is not visible to the orchestrator")

        execution_id = self._execution_id_factory()
        now = self._clock()

        gates = await self._refresh_gates(snapshot)
        graphed = replace(snapshot, gates=gates)
        readiness = evaluate_readiness(graphed, template=self._template)

        outcome = await self._planner.plan(
            case_id=case_id,
            correlation_id=str(execution_id),
            report=readiness,
        )
        await self._record_proposal(snapshot, execution_id, now, outcome)

        created_ids, enqueued_ids = await self._schedule(graphed, outcome.plan)

        deadlock = detect_deadlock(readiness.state_map(), readiness.reason_map())
        if deadlock is not None:
            await self._audit(
                snapshot,
                execution_id,
                now,
                event_type="ORCHESTRATION_DEADLOCK",
                artifact_id=case_id,
                event_data={
                    "reasons": list(deadlock.reasons),
                    "blocked": [
                        {"taskType": task_type.value, "reason": reason}
                        for task_type, reason in deadlock.blocked
                    ],
                },
            )

        await self._audit(
            snapshot,
            execution_id,
            now,
            event_type="ORCHESTRATION_ADVANCED",
            artifact_id=case_id,
            event_data={
                "planSource": outcome.plan.source,
                "proposalStatus": outcome.status,
                "createdTaskIds": [str(task_id) for task_id in created_ids],
                "enqueuedTaskIds": [str(task_id) for task_id in enqueued_ids],
                "readiness": {
                    assessment.task_type.value: assessment.readiness.value
                    for assessment in readiness.assessments
                },
                "supersededTaskIds": list(readiness.superseded_task_ids),
                "deadlock": deadlock is not None,
            },
        )

        return AdvanceResult(
            execution_id=execution_id,
            case_id=case_id,
            case_version=snapshot.case_version,
            recorded_at=now,
            plan=outcome.plan,
            proposal_status=outcome.status,
            validation_errors=outcome.validation_errors,
            readiness=readiness,
            gates=gates,
            created_task_ids=created_ids,
            enqueued_task_ids=enqueued_ids,
            superseded_task_ids=readiness.superseded_task_ids,
            deadlock=deadlock,
        )

    async def _refresh_gates(
        self, snapshot: OrchestrationSnapshot
    ) -> tuple[GateRecord, ...]:
        effective = derive_effective_gates(snapshot)
        persisted: list[GateRecord] = []
        for gate_type, record in effective.items():
            stored = await self._repository.ensure_gate(
                case_id=snapshot.case_id,
                case_version=snapshot.case_version,
                gate_type=gate_type,
                status=record.status,
                satisfied_by_actor_id=record.satisfied_by_actor_id,
                disposition_ref=record.disposition_ref,
            )
            persisted.append(stored)
        return tuple(persisted)

    async def _schedule(
        self,
        snapshot: OrchestrationSnapshot,
        plan: Plan,
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        current_task_id_by_type = {
            task.task_type: task.task_id
            for task in snapshot.tasks
            if task.case_version == snapshot.case_version
        }
        created: list[UUID] = []
        enqueued: list[UUID] = []
        for step in plan.steps:
            node = self._template.by_type[step.task_type]
            depends_on = tuple(
                current_task_id_by_type[predecessor]
                for predecessor in node.predecessors
                if predecessor in current_task_id_by_type
            )
            task_id = self._id_factory()
            result = await self._repository.create_task(
                task_id=task_id,
                case_id=snapshot.case_id,
                case_version=snapshot.case_version,
                task_type=step.task_type,
                idempotency_key=_idempotency_key(
                    snapshot.case_id, snapshot.case_version, step.task_type
                ),
                input_payload={
                    "priority": step.priority,
                    "rationale": step.rationale,
                    "planSource": plan.source,
                },
                depends_on=depends_on,
            )
            if not result.created:
                # A prior advance already created (and enqueued) this task; the
                # unique idempotency key deduplicates, so there is nothing new to
                # enqueue.  Duplicate delivery therefore has no duplicate effect.
                continue
            created.append(result.row.task_id)
            envelope = TaskEnvelopeV1(
                task_id=result.row.task_id,
                case_id=snapshot.case_id,
                case_version=snapshot.case_version,
                task_type=step.task_type,
                document_version_id=None,
            )
            await self._queue.send(envelope)
            enqueued.append(result.row.task_id)
        return tuple(created), tuple(enqueued)

    async def _record_proposal(
        self,
        snapshot: OrchestrationSnapshot,
        execution_id: UUID,
        now: datetime,
        outcome: ProposalOutcome,
    ) -> None:
        proposal_id = self._id_factory()
        proposal_body: dict[str, object] = {
            "source": outcome.plan.source,
            "steps": [
                {"taskType": step.task_type.value, "priority": step.priority}
                for step in outcome.plan.steps
            ],
        }
        if outcome.raw_proposal is not None:
            proposal_body["raw"] = dict(outcome.raw_proposal)
        await self._repository.record_proposal(
            proposal_id=proposal_id,
            case_id=snapshot.case_id,
            case_version=snapshot.case_version,
            execution_id=execution_id,
            proposal=proposal_body,
            status=outcome.status,
            validation_errors=outcome.validation_errors,
            prompt_version=outcome.prompt_version,
            schema_version=outcome.schema_version,
            model_version=outcome.model_version,
        )
        await self._audit(
            snapshot,
            execution_id,
            now,
            event_type="ORCHESTRATION_PLANNER_PROPOSAL",
            artifact_id=proposal_id,
            event_data={
                "status": outcome.status,
                "planSource": outcome.plan.source,
                "validationErrors": list(outcome.validation_errors),
                "promptVersion": outcome.prompt_version,
                "schemaVersion": outcome.schema_version,
                "modelVersion": outcome.model_version,
            },
        )

    async def _audit(
        self,
        snapshot: OrchestrationSnapshot,
        execution_id: UUID,
        now: datetime,
        *,
        event_type: str,
        artifact_id: UUID,
        event_data: dict[str, object],
    ) -> None:
        enriched: dict[str, object] = {
            "role": CASE_ORCHESTRATOR_ROLE,
            "recordedAt": now.isoformat(),
            **event_data,
        }
        await self._repository.append_audit(
            OrchestrationAuditEvent(
                case_id=snapshot.case_id,
                case_version=snapshot.case_version,
                event_type=event_type,
                execution_id=execution_id,
                artifact_type="CREDIT_CASE",
                artifact_id=artifact_id,
                event_data=enriched,
            )
        )
