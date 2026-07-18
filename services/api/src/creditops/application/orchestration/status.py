"""Read-only orchestration status: plan, task states, gates, deadlocks.

Pure derivation over a snapshot — no writes, no model call.  The GET endpoint
and tests share this so the reported state is exactly what the deterministic
engine would enforce on the next advance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from creditops.application.orchestration.gates import derive_effective_gates
from creditops.application.orchestration.graph import (
    Deadlock,
    DependencyTemplate,
    detect_deadlock,
)
from creditops.application.orchestration.planner import Plan, build_default_plan
from creditops.application.orchestration.readiness import (
    ReadinessReport,
    evaluate_readiness,
)
from creditops.application.ports.orchestration import (
    GateRecord,
    OrchestrationSnapshot,
    OrchestrationTaskRow,
)


@dataclass(frozen=True, slots=True)
class OrchestrationStatus:
    case_version: int
    has_intake_handoff: bool
    plan: Plan
    readiness: ReadinessReport
    gates: tuple[GateRecord, ...]
    tasks: tuple[OrchestrationTaskRow, ...]
    deadlock: Deadlock | None


def build_status(
    snapshot: OrchestrationSnapshot,
    *,
    template: DependencyTemplate | None = None,
) -> OrchestrationStatus:
    resolved_template = template or DependencyTemplate.canonical()
    effective = derive_effective_gates(snapshot)
    gates = tuple(effective[gate_type] for gate_type in effective)
    readiness = evaluate_readiness(
        replace(snapshot, gates=gates), template=resolved_template
    )
    return OrchestrationStatus(
        case_version=snapshot.case_version,
        has_intake_handoff=snapshot.has_intake_handoff,
        plan=build_default_plan(readiness, resolved_template),
        readiness=readiness,
        gates=gates,
        tasks=snapshot.tasks,
        deadlock=detect_deadlock(readiness.state_map(), readiness.reason_map()),
    )
