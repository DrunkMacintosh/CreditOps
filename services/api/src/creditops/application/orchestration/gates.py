"""Deterministic human-gate derivation.

Only ``G1_INTAKE_COMPLETE`` is derived by the engine — satisfied by the presence
of the intake handoff.  Every other gate reflects its stored status and stays
OPEN until an authorized human records a disposition; the orchestrator never
satisfies or bypasses them.  This pure function is shared by the read-only
status view and the advance use case so both agree on the effective gate state.

``derive_g3_status`` mirrors the G1 pattern for ``G3_RISK_DISPOSITION``: a
pure, deterministic derivation of whether the gate MAY become SATISFIED, kept
entirely separate from who is allowed to act on it.  Unlike G1 (derived and
written by the engine from a handoff), G3 is derived here but WRITTEN only by
the human-facing disposition API (``api/risk_review.py``) after it records a
disposition — never by the Independent Risk Review Agent itself.  The checker
processor never imports or calls this function.

``derive_g2_status`` and ``derive_g4_status`` extend the same pattern for the
Credit Operations Agent's two human-approval surfaces: G2 (document-request
approval, mirrored from ``G2_GAP_REQUEST_APPROVAL``'s existing role gating
INDEPENDENT_RISK_REVIEW readiness) and G4 (``G4_OPS_AUTHORIZATION``, action
authorization).  Both are derived here but WRITTEN only by
``api/credit_ops.py`` after it records a human approval/authorization record
— never by the Credit Operations Agent's worker processor, which never
imports or calls either function.  Writing G2 here does not change how
INDEPENDENT_RISK_REVIEW's own readiness is evaluated (application/orchestration/
readiness.py still reads whatever G2 status is stored for the case version,
regardless of which human-facing endpoint wrote it); ``ensure_gate`` is an
idempotent insert-if-absent-then-satisfy-only-if-OPEN write, so an additional
writer for the same gate type is always safe.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from uuid import UUID

from creditops.application.ports.orchestration import GateRecord, OrchestrationSnapshot
from creditops.domain.orchestration import GateStatus, GateType
from creditops.domain.risk_review import ChallengeSeverity

INTAKE_DISPOSITION_REF = "intake-handoff"

#: Named threshold (deliverable 4): a challenge at or above this severity
#: requires its own human disposition before G3 may derive SATISFIED.
G3_SEVERITY_THRESHOLD: ChallengeSeverity = ChallengeSeverity.HIGH

_SEVERITY_ORDER: Mapping[ChallengeSeverity, int] = {
    ChallengeSeverity.LOW: 0,
    ChallengeSeverity.MEDIUM: 1,
    ChallengeSeverity.HIGH: 2,
    ChallengeSeverity.CRITICAL: 3,
}

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


def derive_g3_status(
    *,
    assessment_exists: bool,
    challenge_severities: Mapping[UUID, ChallengeSeverity],
    disposed_challenge_ids: Set[UUID],
    has_assessment_level_disposition: bool,
    severity_threshold: ChallengeSeverity = G3_SEVERITY_THRESHOLD,
) -> GateStatus:
    """Whether G3_RISK_DISPOSITION MAY derive SATISFIED right now.

    SATISFIED requires ALL of:

    - a checker (Independent Risk Review) assessment exists for the case
      version -- silence is never satisfaction, an empty/never-run review
      cannot satisfy the gate;
    - every challenge at or above ``severity_threshold`` has its own human
      disposition (``disposed_challenge_ids``); and
    - when there are ZERO such severe challenges, an explicit
      assessment-level human disposition still exists (a human NOTED the
      empty-challenge outcome) -- G3 can never derive SATISFIED purely
      because the checker happened to find nothing severe.

    The checker's own output can never satisfy this on its own: every path
    to SATISFIED requires at least one entry in ``disposed_challenge_ids`` or
    ``has_assessment_level_disposition``, both of which are populated
    exclusively from ``challenge_dispositions`` rows -- append-only, human-
    authored, actor-and-role-captured (supabase/migrations/202607180005_
    risk_review.sql).
    """

    if not assessment_exists:
        return GateStatus.OPEN
    severe_ids = {
        challenge_id
        for challenge_id, severity in challenge_severities.items()
        if _SEVERITY_ORDER[severity] >= _SEVERITY_ORDER[severity_threshold]
    }
    if severe_ids:
        return (
            GateStatus.SATISFIED
            if severe_ids <= set(disposed_challenge_ids)
            else GateStatus.OPEN
        )
    return GateStatus.SATISFIED if has_assessment_level_disposition else GateStatus.OPEN


def derive_g2_status(
    *,
    package_exists: bool,
    request_ids: Set[UUID],
    approved_request_ids: Set[UUID],
) -> GateStatus:
    """Whether G2_GAP_REQUEST_APPROVAL MAY derive SATISFIED for the credit-ops
    document-request batch attached to the latest package.

    SATISFIED requires a credit-ops package to exist for the case version AND
    every drafted document request in it to have its own human approval
    record (``request_ids <= approved_request_ids``; vacuously true when
    there are zero drafted requests -- there is nothing to approve).  No
    credit-ops agent code calls this; only the human-facing approval
    endpoint (``api/credit_ops.py``) does, after it records an approval.
    """

    if not package_exists:
        return GateStatus.OPEN
    return GateStatus.SATISFIED if request_ids <= approved_request_ids else GateStatus.OPEN


def derive_g4_status(
    *,
    package_exists: bool,
    action_ids: Set[UUID],
    authorized_action_ids: Set[UUID],
) -> GateStatus:
    """Whether G4_OPS_AUTHORIZATION MAY derive SATISFIED for the proposed
    actions in the latest credit-ops package.

    Mirrors ``derive_g3_status``: SATISFIED requires a credit-ops package to
    exist AND every proposed action to have its own human authorization
    record (``action_ids <= authorized_action_ids``; vacuously true when
    there are zero proposed actions).  The credit-ops worker's own output
    can never satisfy this on its own -- every SATISFIED path requires at
    least one row in ``ops_action_authorizations``, append-only and
    human-authored, actor-and-role-captured
    (supabase/migrations/202607180006_credit_ops.sql).  Authorization only
    RECORDS authority; no code path anywhere in this codebase executes a
    proposed action.
    """

    if not package_exists:
        return GateStatus.OPEN
    return GateStatus.SATISFIED if action_ids <= authorized_action_ids else GateStatus.OPEN
