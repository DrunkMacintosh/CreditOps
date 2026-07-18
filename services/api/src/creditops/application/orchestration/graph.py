"""Canonical dependency template and structural checks (cycle, deadlock).

The template encodes the settled dependency-aware execution pattern from
docs/AGENT_ARCHITECTURE.md as immutable data:

    INTAKE (handoff READY_FOR_SPECIALIST_REVIEW)
      -> CREDIT_UNDERWRITING and LEGAL_COMPLIANCE_COLLATERAL (parallel)
      -> evidence-gap resolution gate
      -> INDEPENDENT_RISK_REVIEW
      -> CREDIT_OPERATIONS

Each node names its predecessor task types and the single human gate that must be
SATISFIED before it may become ready.  The template is validated to be acyclic at
import time so a malformed edit fails fast rather than deadlocking a case.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from creditops.domain.orchestration import GateType, TaskReadiness, TaskType


@dataclass(frozen=True, slots=True)
class TaskNode:
    task_type: TaskType
    predecessors: tuple[TaskType, ...]
    required_gate: GateType | None


# The orchestrated specialist nodes.  DOCUMENT_INGESTION is the pre-existing
# per-document pipeline (not orchestrated here) and ORCHESTRATOR_PLAN is the
# planner driver itself (created by the kick-off, not scheduled as a node).
_TEMPLATE_NODES: tuple[TaskNode, ...] = (
    TaskNode(
        TaskType.CREDIT_UNDERWRITING,
        predecessors=(),
        required_gate=GateType.G1_INTAKE_COMPLETE,
    ),
    TaskNode(
        TaskType.LEGAL_COMPLIANCE_COLLATERAL,
        predecessors=(),
        required_gate=GateType.G1_INTAKE_COMPLETE,
    ),
    TaskNode(
        TaskType.INDEPENDENT_RISK_REVIEW,
        predecessors=(TaskType.CREDIT_UNDERWRITING, TaskType.LEGAL_COMPLIANCE_COLLATERAL),
        required_gate=GateType.G2_GAP_REQUEST_APPROVAL,
    ),
    TaskNode(
        TaskType.CREDIT_OPERATIONS,
        predecessors=(TaskType.INDEPENDENT_RISK_REVIEW,),
        required_gate=GateType.G3_RISK_DISPOSITION,
    ),
)


@dataclass(frozen=True, slots=True)
class DependencyTemplate:
    """Immutable canonical template plus indexed lookups."""

    nodes: tuple[TaskNode, ...]
    by_type: Mapping[TaskType, TaskNode] = field(default_factory=dict)

    @classmethod
    def canonical(cls) -> DependencyTemplate:
        by_type = {node.task_type: node for node in _TEMPLATE_NODES}
        template = cls(nodes=_TEMPLATE_NODES, by_type=MappingProxyType(by_type))
        cycle = detect_cycle(template.edges())
        if cycle is not None:
            raise ValueError(f"canonical dependency template contains a cycle: {cycle}")
        return template

    def edges(self) -> Mapping[TaskType, tuple[TaskType, ...]]:
        return {node.task_type: node.predecessors for node in self.nodes}

    def ordered_types(self) -> tuple[TaskType, ...]:
        return tuple(node.task_type for node in self.nodes)

    def priority_of(self, task_type: TaskType) -> int:
        for index, node in enumerate(self.nodes):
            if node.task_type is task_type:
                return index
        return len(self.nodes)


def detect_cycle(
    edges: Mapping[TaskType, tuple[TaskType, ...]],
) -> tuple[TaskType, ...] | None:
    """Return one cycle as an ordered tuple, or ``None`` if the graph is a DAG.

    ``edges[node]`` lists the node's predecessors (incoming dependencies).
    """

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[TaskType, int] = {node: WHITE for node in edges}
    stack: list[TaskType] = []

    def visit(node: TaskType) -> tuple[TaskType, ...] | None:
        color[node] = GREY
        stack.append(node)
        for predecessor in edges.get(node, ()):  # predecessors may be outside the map
            state = color.get(predecessor, WHITE)
            if state == GREY:
                start = stack.index(predecessor)
                return tuple(stack[start:]) + (predecessor,)
            if state == WHITE:
                found = visit(predecessor)
                if found is not None:
                    return found
        color[node] = BLACK
        stack.pop()
        return None

    for node in edges:
        if color[node] == WHITE:
            found = visit(node)
            if found is not None:
                return found
    return None


@dataclass(frozen=True, slots=True)
class Deadlock:
    """A surfaced stall: incomplete work with nothing ready and nothing running.

    This is never raised silently.  It covers both a true structural stall and
    the expected case of work parked behind an OPEN human gate; the reasons list
    makes the distinction visible to the API and audit trail.
    """

    blocked: tuple[tuple[TaskType, str], ...]
    reasons: tuple[str, ...]


def detect_deadlock(
    assessments: Mapping[TaskType, TaskReadiness],
    reasons: Mapping[TaskType, str],
) -> Deadlock | None:
    """Surface a stall when no node is READY or IN_PROGRESS but work remains."""

    incomplete = {
        task_type: state
        for task_type, state in assessments.items()
        if state
        not in (
            TaskReadiness.COMPLETE,
            TaskReadiness.SUPERSEDED,
        )
    }
    has_progress = any(
        state in (TaskReadiness.READY, TaskReadiness.IN_PROGRESS)
        for state in assessments.values()
    )
    remaining_work = any(
        state not in (TaskReadiness.COMPLETE, TaskReadiness.SUPERSEDED)
        for state in assessments.values()
    )
    if has_progress or not remaining_work:
        return None
    blocked = tuple(
        (task_type, reasons.get(task_type, "blocked"))
        for task_type in incomplete
    )
    distinct_reasons = tuple(dict.fromkeys(reason for _, reason in blocked))
    return Deadlock(blocked=blocked, reasons=distinct_reasons)
