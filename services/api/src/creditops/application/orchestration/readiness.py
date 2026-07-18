"""Deterministic readiness evaluation for the task graph.

Readiness is a pure function of durable state — dependency completion, blocking
evidence gaps, human-gate status, and case-version currency — and is computed
fresh every time; it is never stored.  This is the model-free enforcement layer
required by ADR-0001: no planner proposal can make a not-ready node ready.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.ports.orchestration import OrchestrationSnapshot
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import (
    GateStatus,
    GateType,
    TaskReadiness,
    TaskType,
)

_TERMINAL_SUCCESS = frozenset({TaskStatus.SUCCEEDED})
_IN_FLIGHT = frozenset({TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRY_WAIT})


@dataclass(frozen=True, slots=True)
class NodeAssessment:
    task_type: TaskType
    readiness: TaskReadiness
    reason: str


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    assessments: tuple[NodeAssessment, ...]
    superseded_task_ids: tuple[str, ...] = ()

    def by_type(self, task_type: TaskType) -> NodeAssessment:
        for assessment in self.assessments:
            if assessment.task_type is task_type:
                return assessment
        raise KeyError(task_type)

    def ready_types(self) -> tuple[TaskType, ...]:
        return tuple(
            assessment.task_type
            for assessment in self.assessments
            if assessment.readiness is TaskReadiness.READY
        )

    def state_map(self) -> Mapping[TaskType, TaskReadiness]:
        return {a.task_type: a.readiness for a in self.assessments}

    def reason_map(self) -> Mapping[TaskType, str]:
        return {a.task_type: a.reason for a in self.assessments}


def evaluate_readiness(
    snapshot: OrchestrationSnapshot,
    *,
    template: DependencyTemplate,
) -> ReadinessReport:
    current_version = snapshot.case_version
    gate_status: dict[GateType, GateStatus] = {
        gate.gate_type: gate.status
        for gate in snapshot.gates
        if gate.case_version == current_version
    }

    # Map each unresolved BLOCKING gap to the task type it is attached to, so a
    # candidate whose inputs have a blocking gap is never started (property b).
    task_type_by_id = {task.task_id: task.task_type for task in snapshot.tasks}
    blocked_by_gap: set[TaskType] = set()
    for gap in snapshot.blocking_gaps:
        if gap.blocking_level != "BLOCKING":
            continue
        affected = task_type_by_id.get(gap.affected_task_id)
        if affected is not None:
            blocked_by_gap.add(affected)

    # Live current-version task per type (a SUPERSEDED attempt is void and does
    # not represent the node), plus any stale-version task to fence.
    current_task = {
        task.task_type: task
        for task in snapshot.tasks
        if task.case_version == current_version and task.status is not TaskStatus.SUPERSEDED
    }
    superseded_ids = tuple(
        str(task.task_id)
        for task in snapshot.tasks
        if task.case_version < current_version
    )

    assessments: list[NodeAssessment] = []
    resolved: dict[TaskType, TaskReadiness] = {}
    for node in template.nodes:
        assessment = _assess_node(
            node_type=node.task_type,
            predecessors=node.predecessors,
            required_gate=node.required_gate,
            current_task_status=(
                current_task[node.task_type].status
                if node.task_type in current_task
                else None
            ),
            has_stale_task=any(
                task.task_type is node.task_type and task.case_version < current_version
                for task in snapshot.tasks
            ),
            gate_status=gate_status,
            blocked_by_gap=blocked_by_gap,
            resolved=resolved,
        )
        assessments.append(assessment)
        resolved[node.task_type] = assessment.readiness

    return ReadinessReport(
        assessments=tuple(assessments),
        superseded_task_ids=superseded_ids,
    )


def _assess_node(
    *,
    node_type: TaskType,
    predecessors: tuple[TaskType, ...],
    required_gate: GateType | None,
    current_task_status: TaskStatus | None,
    has_stale_task: bool,
    gate_status: Mapping[GateType, GateStatus],
    blocked_by_gap: set[TaskType],
    resolved: Mapping[TaskType, TaskReadiness],
) -> NodeAssessment:
    if current_task_status is not None:
        if current_task_status in _TERMINAL_SUCCESS:
            return NodeAssessment(node_type, TaskReadiness.COMPLETE, "task succeeded")
        if current_task_status in _IN_FLIGHT:
            return NodeAssessment(node_type, TaskReadiness.IN_PROGRESS, "task in progress")
        if current_task_status is TaskStatus.FAILED_MANUAL_REVIEW:
            return NodeAssessment(
                node_type, TaskReadiness.FAILED, "task in manual review"
            )
        # A SUPERSEDED attempt is void, not terminal for the node: fall through
        # so fresh work is assessed against gaps, gates, and dependencies.

    # No live current-version task.  A task existing only at an older case
    # version is fenced (superseded); a fresh one may be scheduled when ready.
    if node_type in blocked_by_gap:
        return NodeAssessment(
            node_type, TaskReadiness.BLOCKED, "blocking evidence gap on required inputs"
        )
    if required_gate is not None and gate_status.get(required_gate) is not GateStatus.SATISFIED:
        return NodeAssessment(
            node_type,
            TaskReadiness.BLOCKED,
            f"human gate {required_gate.value} is not satisfied",
        )
    unmet = [
        predecessor
        for predecessor in predecessors
        if resolved.get(predecessor) is not TaskReadiness.COMPLETE
    ]
    if unmet:
        joined = ", ".join(predecessor.value for predecessor in unmet)
        return NodeAssessment(
            node_type, TaskReadiness.BLOCKED, f"waiting for predecessor: {joined}"
        )
    detail = "stale task fenced; fresh work is ready" if has_stale_task else "dependencies met"
    return NodeAssessment(node_type, TaskReadiness.READY, detail)
