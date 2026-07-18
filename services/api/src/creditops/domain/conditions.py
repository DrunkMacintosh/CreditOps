"""Disbursement ConditionLedger: the stage-10 typed disbursement-condition domain.

Master design section 5 giai đoạn 10 ("Kiểm tra điều kiện giải ngân").
Independent credit operations verify that the signed contract, perfected
security, customer own-funds participation, purpose documents, licences and any
other bound conditions are satisfied BEFORE disbursement.  This module encodes
the pure, deterministic core of that ledger:

- ``ConditionStatus`` is the CLOSED spec status set (exactly the 8 values named
  in the design: PENDING, EVIDENCE_SUBMITTED, VERIFIED, FAILED,
  WAIVER_REQUESTED, WAIVED_BY_HUMAN, SUPERSEDED, NOT_APPLICABLE_BY_HUMAN).
- ``ALLOWED_TRANSITIONS`` is the deterministic transition map.  A transition
  that is not an explicit pair in the map is rejected -- there is no implicit or
  "any -> any" edge.  The same map is re-encoded (defence in depth) by the
  database trigger (supabase/migrations/202607180018_condition_ledger.sql) and
  re-checked by the application layer before every write.
- ``DisbursementCondition`` is a frozen value object; every condition binds its
  SOURCE decision (``decision_id``), the exact case version, an owner, an
  optional due date, and its evidence references.  The VERIFIER and verification
  time are captured on the append-only status-event trail, not mutated onto the
  row (the row's only mutable columns are ``status`` and ``evidence_refs``).
- ``derive_conditions_confirmable`` is the pure gate predicate: the ledger is
  confirmable IFF it is NON-EMPTY and every condition is terminally satisfied
  (VERIFIED, WAIVED_BY_HUMAN or NOT_APPLICABLE_BY_HUMAN).  An EMPTY ledger is
  NEVER confirmable -- see the no-vacuous-confirmation note below.

PROPOSED / SYNTHETIC: the status taxonomy, the exact transition edges, and the
authority split (who may drive which transition) are a prototype configuration.
No official SHB disbursement-condition control code, authority matrix, or
materiality threshold has been supplied; every choice below is documented as
PROPOSED and MUST be reconfigured when an official source exists.

NO-VACUOUS-CONFIRMATION: a case with genuinely zero conditions must NOT satisfy
``HG_DISBURSEMENT_CONDITIONS_CONFIRMED`` by silence.  The human instead records
ONE explicit synthetic condition ("không có điều kiện giải ngân") and disposes
it ``NOT_APPLICABLE_BY_HUMAN`` with a rationale -- an auditable human act -- so
the confirmation is always backed by at least one deliberate human disposition,
never by an empty set.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from creditops.domain.ids import CaseId

type DisbursementConditionId = UUID


class ConditionStatus(StrEnum):
    """The CLOSED set of disbursement-condition statuses (design giai đoạn 10).

    PROPOSED synthetic taxonomy: no official SHB condition-status vocabulary has
    been supplied.  An agent can never WRITE any of these values -- a status is
    moved exclusively by an authorized human through ``api/conditions.py`` and
    only along an ``ALLOWED_TRANSITIONS`` edge.
    """

    PENDING = "PENDING"
    EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    WAIVER_REQUESTED = "WAIVER_REQUESTED"
    WAIVED_BY_HUMAN = "WAIVED_BY_HUMAN"
    SUPERSEDED = "SUPERSEDED"
    NOT_APPLICABLE_BY_HUMAN = "NOT_APPLICABLE_BY_HUMAN"


#: The deterministic transition map (PROPOSED synthetic edges).  A pair absent
#: here is FORBIDDEN -- there is no implicit edge.  Rationale per source state:
#:
#: - ``PENDING`` -> the condition can gather evidence (``EVIDENCE_SUBMITTED``),
#:   be routed for a human waiver (``WAIVER_REQUESTED``), be ruled inapplicable
#:   by a human (``NOT_APPLICABLE_BY_HUMAN``), or be invalidated by a new case
#:   version (``SUPERSEDED``).  It may NOT jump straight to ``VERIFIED``:
#:   verification always follows submitted evidence, never silence.
#: - ``EVIDENCE_SUBMITTED`` -> a human checker rules the evidence sufficient
#:   (``VERIFIED``) or insufficient (``FAILED``), or the version supersedes it.
#: - ``FAILED`` -> re-submit corrected evidence (``EVIDENCE_SUBMITTED``), route
#:   to a human waiver (``WAIVER_REQUESTED``), or supersede.  It may NOT go
#:   directly to ``VERIFIED`` -- fixing a failure means re-submitting evidence
#:   for a fresh check, never flipping the verdict in place.
#: - ``WAIVER_REQUESTED`` -> a human grants the waiver (``WAIVED_BY_HUMAN``),
#:   refuses it (``FAILED``), or the version supersedes it.  There is no path
#:   back to ``PENDING``/``EVIDENCE_SUBMITTED``: a refused waiver is a failure.
#: - ``VERIFIED`` / ``WAIVED_BY_HUMAN`` / ``NOT_APPLICABLE_BY_HUMAN`` are the
#:   satisfied terminals: the ONLY edge out is ``SUPERSEDED`` (a new case
#:   version invalidates the prior version's ledger; a satisfied verdict is
#:   otherwise immutable, never silently downgraded).
#: - ``SUPERSEDED`` is fully terminal: no outgoing edges.
ALLOWED_TRANSITIONS: Mapping[ConditionStatus, frozenset[ConditionStatus]] = {
    ConditionStatus.PENDING: frozenset(
        {
            ConditionStatus.EVIDENCE_SUBMITTED,
            ConditionStatus.WAIVER_REQUESTED,
            ConditionStatus.NOT_APPLICABLE_BY_HUMAN,
            ConditionStatus.SUPERSEDED,
        }
    ),
    ConditionStatus.EVIDENCE_SUBMITTED: frozenset(
        {
            ConditionStatus.VERIFIED,
            ConditionStatus.FAILED,
            ConditionStatus.SUPERSEDED,
        }
    ),
    ConditionStatus.FAILED: frozenset(
        {
            ConditionStatus.EVIDENCE_SUBMITTED,
            ConditionStatus.WAIVER_REQUESTED,
            ConditionStatus.SUPERSEDED,
        }
    ),
    ConditionStatus.WAIVER_REQUESTED: frozenset(
        {
            ConditionStatus.WAIVED_BY_HUMAN,
            ConditionStatus.FAILED,
            ConditionStatus.SUPERSEDED,
        }
    ),
    ConditionStatus.VERIFIED: frozenset({ConditionStatus.SUPERSEDED}),
    ConditionStatus.WAIVED_BY_HUMAN: frozenset({ConditionStatus.SUPERSEDED}),
    ConditionStatus.NOT_APPLICABLE_BY_HUMAN: frozenset({ConditionStatus.SUPERSEDED}),
    ConditionStatus.SUPERSEDED: frozenset(),
}

#: The satisfied terminals a ledger may be confirmed on (design giai đoạn 10:
#: "deterministic rule và human checker xác nhận").  A ledger is confirmable
#: only when EVERY condition is in this set.
CONFIRMABLE_STATUSES: frozenset[ConditionStatus] = frozenset(
    {
        ConditionStatus.VERIFIED,
        ConditionStatus.WAIVED_BY_HUMAN,
        ConditionStatus.NOT_APPLICABLE_BY_HUMAN,
    }
)

#: Transition TARGETS that are a HUMAN authority act and therefore REQUIRE an
#: explicit authority rationale (PROPOSED: "waiver luôn human-only và phải có
#: authority record").  Both a waiver and a human not-applicable ruling are
#: human dispositions that must carry a recorded rationale + actor + role.
RATIONALE_REQUIRED_TARGETS: frozenset[ConditionStatus] = frozenset(
    {
        ConditionStatus.WAIVED_BY_HUMAN,
        ConditionStatus.NOT_APPLICABLE_BY_HUMAN,
    }
)

#: Transition TARGETS reserved to the independent OPS checker authority
#: (PROPOSED synthetic): verification is an independent-checker act, and a
#: human waiver / not-applicable ruling is an authority disposition.  The
#: remaining targets are ordinary ops-officer workflow moves.
CHECKER_AUTHORITY_TARGETS: frozenset[ConditionStatus] = frozenset(
    {
        ConditionStatus.VERIFIED,
        ConditionStatus.WAIVED_BY_HUMAN,
        ConditionStatus.NOT_APPLICABLE_BY_HUMAN,
    }
)


def is_transition_allowed(
    from_status: ConditionStatus, to_status: ConditionStatus
) -> bool:
    """Whether ``from_status -> to_status`` is an explicit allowed edge.

    Self-transitions and any pair absent from ``ALLOWED_TRANSITIONS`` are
    rejected: the map is exhaustive and there is no implicit edge.
    """

    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


class DisbursementCondition(BaseModel):
    """One disbursement condition bound to its source credit decision.

    Frozen value object.  ``status`` and ``evidence_refs`` are the only fields a
    later UPDATE may change (trigger-enforced in the migration); everything else
    is bound at creation.  The verifier and verification time live on the
    append-only status-event trail, never mutated onto this row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: DisbursementConditionId
    case_id: CaseId
    case_version: int = Field(ge=1)
    decision_id: UUID
    condition_text_vi: str = Field(min_length=1, max_length=4000)
    owner_vi: str | None = Field(default=None, min_length=1, max_length=400)
    due_date: date | None = None
    status: ConditionStatus = ConditionStatus.PENDING
    evidence_refs: tuple[str, ...] = ()
    created_at: datetime | None = None


def derive_conditions_confirmable(statuses: Iterable[ConditionStatus]) -> bool:
    """Whether the disbursement-condition ledger MAY be confirmed right now.

    True IFF the ledger is NON-EMPTY and every condition status is a satisfied
    terminal (``CONFIRMABLE_STATUSES``).  An EMPTY ledger is deliberately NOT
    confirmable: ``HG_DISBURSEMENT_CONDITIONS_CONFIRMED`` must never be
    satisfied by silence.  A case with genuinely no conditions records ONE
    explicit ``NOT_APPLICABLE_BY_HUMAN`` entry so confirmation always rests on a
    deliberate human disposition, never a vacuous truth over an empty set.
    """

    materialized = list(statuses)
    if not materialized:
        return False
    return all(status in CONFIRMABLE_STATUSES for status in materialized)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "CHECKER_AUTHORITY_TARGETS",
    "CONFIRMABLE_STATUSES",
    "RATIONALE_REQUIRED_TARGETS",
    "ConditionStatus",
    "DisbursementCondition",
    "DisbursementConditionId",
    "derive_conditions_confirmable",
    "is_transition_allowed",
]
