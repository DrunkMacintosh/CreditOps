"""Deterministic human-gate derivation.

Only ``G1_INTAKE_COMPLETE`` is derived by the engine — satisfied by the presence
of the intake handoff.  Every other gate reflects its stored status and stays
OPEN until an authorized human records a disposition; the orchestrator never
satisfies or bypasses them.  This pure function is shared by the read-only
status view and the advance use case so both agree on the effective gate state.
"""

from __future__ import annotations

from collections.abc import Mapping

from creditops.application.ports.orchestration import GateRecord, OrchestrationSnapshot
from creditops.domain.orchestration import GateStatus, GateType

INTAKE_DISPOSITION_REF = "intake-handoff"

ALL_GATES: tuple[GateType, ...] = (
    GateType.G1_INTAKE_COMPLETE,
    GateType.G2_GAP_REQUEST_APPROVAL,
    GateType.G3_RISK_DISPOSITION,
    GateType.G4_OPS_AUTHORIZATION,
)


def derive_effective_gates(
    snapshot: OrchestrationSnapshot,
) -> Mapping[GateType, GateRecord]:
    stored: dict[GateType, GateRecord] = {
        gate.gate_type: gate
        for gate in snapshot.gates
        if gate.case_version == snapshot.case_version
    }
    effective: dict[GateType, GateRecord] = {}
    for gate_type in ALL_GATES:
        existing = stored.get(gate_type)
        if gate_type is GateType.G1_INTAKE_COMPLETE:
            # A gate, once satisfied, is immutable; otherwise it is satisfied iff
            # the intake handoff exists.
            if existing is not None and existing.status is GateStatus.SATISFIED:
                effective[gate_type] = existing
            elif snapshot.has_intake_handoff:
                effective[gate_type] = GateRecord(
                    gate_type=gate_type,
                    case_version=snapshot.case_version,
                    status=GateStatus.SATISFIED,
                    disposition_ref=INTAKE_DISPOSITION_REF,
                )
            else:
                effective[gate_type] = GateRecord(
                    gate_type=gate_type,
                    case_version=snapshot.case_version,
                    status=GateStatus.OPEN,
                )
            continue
        # G2/G3/G4: reflect the stored disposition, defaulting to OPEN.  The
        # engine never advances these; only a human disposition can.
        effective[gate_type] = existing or GateRecord(
            gate_type=gate_type,
            case_version=snapshot.case_version,
            status=GateStatus.OPEN,
        )
    return effective
