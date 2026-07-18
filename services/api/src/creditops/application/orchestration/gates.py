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

``G2_GAP_REQUEST_APPROVAL`` is NO LONGER derived here.  It is the pre-Risk
Evidence-Gap workflow gate (the ``HG_OUTBOUND_REQUEST_APPROVED`` capability,
master design section 9): it is derived ONLY from a ``GapRequestBatch`` plus a
human disposition, by ``domain/gap_request_batches.derive_g2_from_batch``, and
WRITTEN only by ``api/gap_requests.py``.  It no longer references the credit-ops
package in any way -- the old package-based G2 derivation and its credit-ops
writer have both been deleted, which is what breaks the
Risk-waits-on-Credit-Operations cycle.  ``INDEPENDENT_RISK_REVIEW`` still gates
on the STORED G2 status for the case version (readiness.py reads whatever is
stored, regardless of which endpoint wrote it), so a satisfied G2 recorded from
a batch disposition -- with zero credit-ops packages anywhere -- makes Risk
ready.

``derive_g4_status`` keeps the Credit Operations pattern for G4
(``G4_OPS_AUTHORIZATION``, action authorization): derived here, WRITTEN only by
``api/credit_ops.py`` after it records a human authorization record -- never by
the Credit Operations Agent's worker processor.  ``ensure_gate`` is an
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

#: PROPOSED synthetic configuration -- no official SHB disposition taxonomy
#: exists.  "Đã disposition" is NOT "được tiếp tục" (master design section
#: 6): only these continue-authorizing types may satisfy
#: ``G3_RISK_DISPOSITION``.  ``MAKER_MUST_REVISE`` demands a maker revision
#: (Credit Operations must never open from it) and ``ESCALATED`` awaits a
#: higher-authority outcome; both leave the gate OPEN, fail closed.
G3_CONTINUE_DISPOSITION_TYPES: frozenset[str] = frozenset({"NOTED", "ACCEPTED_RISK"})

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
    latest_challenge_dispositions: Mapping[UUID, str],
    latest_assessment_level_disposition: str | None,
    severity_threshold: ChallengeSeverity = G3_SEVERITY_THRESHOLD,
) -> GateStatus:
    """Whether G3_RISK_DISPOSITION MAY derive SATISFIED right now.

    SATISFIED requires ALL of:

    - a checker (Independent Risk Review) assessment exists for the case
      version -- silence is never satisfaction, an empty/never-run review
      cannot satisfy the gate;
    - every challenge at or above ``severity_threshold`` has a human
      disposition whose LATEST type is continue-authorizing
      (``G3_CONTINUE_DISPOSITION_TYPES``) -- a MAKER_MUST_REVISE or
      ESCALATED latest disposition leaves the gate OPEN because "đã
      disposition" is not "được tiếp tục"; and
    - when there are ZERO such severe challenges, an explicit
      assessment-level human disposition with a continue-authorizing type
      still exists (a human NOTED the empty-challenge outcome) -- G3 can
      never derive SATISFIED purely because the checker happened to find
      nothing severe.

    The checker's own output can never satisfy this on its own: every path
    to SATISFIED requires at least one entry in
    ``latest_challenge_dispositions`` or a non-``None``
    ``latest_assessment_level_disposition``, both of which are populated
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
            if all(
                latest_challenge_dispositions.get(challenge_id)
                in G3_CONTINUE_DISPOSITION_TYPES
                for challenge_id in severe_ids
            )
            else GateStatus.OPEN
        )
    return (
        GateStatus.SATISFIED
        if latest_assessment_level_disposition in G3_CONTINUE_DISPOSITION_TYPES
        else GateStatus.OPEN
    )


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
