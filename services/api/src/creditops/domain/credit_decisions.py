"""Human Credit Decision + Approved Term Snapshot: the stage-6 authority record.

Master design section 5 stage 6 and section 13 "decisions" family
(``human_credit_decisions``, ``approved_term_snapshots``); P0 #9 remainder.

SPEC CONTRACT faithfully encoded here:

- ONLY a human actor with decision authority records a credit decision.  There
  is no agent path to this module: it is imported by the human-only API
  (``api/credit_decisions.py``) and its Postgres adapter, and by nothing in the
  agent worker.
- ``CreditDecisionType`` is a PROPOSED synthetic taxonomy (labelled on the enum
  below), NOT an official SHB decision vocabulary; it must be reconfigured when
  an official source is supplied.
- A decision binds the EXACT case version and the exact memo/assessment artifact
  versions the decider reviewed (``memo_artifact_id``, ``risk_assessment_id``,
  ``underwriting_assessment_id``).  These are pure identifiers here; the binding
  is proved fail-closed against the current case state at the application layer.
- ``APPROVED_WITH_CONDITIONS`` requires at least one non-empty condition; every
  other decision type carries no conditions.
- An ``ApprovedTermSnapshot`` freezes the approved terms at decision time and is
  1:1 with its decision.  ``DECLINED_BY_HUMAN``, ``RETURNED_FOR_REVISION`` and
  ``MORE_INFORMATION_REQUIRED`` forbid a snapshot (there are no approved terms
  to freeze).
- Everything here is a pure, frozen value object.  Append-only semantics,
  idempotency and one-decision-per-version live in the migration and adapter.

All data is synthetic and created solely for demonstration.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId

type HumanCreditDecisionId = UUID
type ApprovedTermSnapshotId = UUID

_SHA256_HEX = r"^[0-9a-f]{64}$"


class CreditDecisionType(StrEnum):
    """The closed set of human credit-decision outcomes.

    PROPOSED synthetic taxonomy (master design sections 4 and 5 stage 6): no
    official SHB decision vocabulary, authority matrix, or materiality
    threshold has been supplied, so these labels are a prototype configuration
    only and MUST be reconfigured once an official source exists.  An agent can
    never write any of these values -- the decision is recorded exclusively by
    an authorized human through ``api/credit_decisions.py``.
    """

    APPROVED_AS_PROPOSED = "APPROVED_AS_PROPOSED"
    APPROVED_WITH_CONDITIONS = "APPROVED_WITH_CONDITIONS"
    RETURNED_FOR_REVISION = "RETURNED_FOR_REVISION"
    MORE_INFORMATION_REQUIRED = "MORE_INFORMATION_REQUIRED"
    DECLINED_BY_HUMAN = "DECLINED_BY_HUMAN"


#: Outcomes that MAY carry an ``ApprovedTermSnapshot`` (there are approved terms
#: to freeze).
APPROVAL_DECISIONS: frozenset[CreditDecisionType] = frozenset(
    {
        CreditDecisionType.APPROVED_AS_PROPOSED,
        CreditDecisionType.APPROVED_WITH_CONDITIONS,
    }
)

#: Outcomes that FORBID an ``ApprovedTermSnapshot`` -- the complement of
#: ``APPROVAL_DECISIONS``.  A snapshot on any of these is a contract violation.
SNAPSHOT_FORBIDDING_DECISIONS: frozenset[CreditDecisionType] = frozenset(
    set(CreditDecisionType) - APPROVAL_DECISIONS
)


class ApprovedTerms(BaseModel):
    """The frozen approved credit terms.  Every field is nullable INSIDE the
    object -- a decision may approve terms while leaving individual fields
    unspecified.  ``amount`` and ``rate`` are exact decimals (design section 11:
    no float arithmetic on money)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    term: str | None = Field(default=None, min_length=1, max_length=100)
    rate: Decimal | None = None


def compute_terms_hash(terms: ApprovedTerms) -> str:
    """Canonical-JSON sha256 hex over the approved terms (pure; no I/O).

    Mirrors ``domain/gap_request_batches.compute_open_gap_snapshot_hash``:
    ``sort_keys`` + tight separators make the hash stable across
    re-serialisation, and exact decimals serialise deterministically to
    strings in json mode, so the same terms always hash identically.
    """

    canonical = json.dumps(
        terms.model_dump(mode="json"),
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class HumanCreditDecision(BaseModel):
    """One human credit decision for one exact case version.

    Append-only once persisted; one per case version (a revision bumps the
    version).  ``conditions`` is coupled to the decision type: required and
    non-empty for ``APPROVED_WITH_CONDITIONS``, empty for everything else.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: HumanCreditDecisionId
    case_id: CaseId
    case_version: int = Field(ge=1)
    decision: CreditDecisionType
    rationale_vi: str = Field(min_length=1, max_length=4000)
    decided_by: UUID
    decided_by_role: str = Field(min_length=1, max_length=100)
    memo_artifact_id: UUID | None = None
    risk_assessment_id: UUID | None = None
    underwriting_assessment_id: UUID | None = None
    conditions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _conditions_match_decision(self) -> Self:
        has_conditions = bool(self.conditions)
        if self.decision is CreditDecisionType.APPROVED_WITH_CONDITIONS:
            if not has_conditions:
                raise ValueError(
                    "APPROVED_WITH_CONDITIONS requires at least one condition"
                )
            if any(not condition.strip() for condition in self.conditions):
                raise ValueError("every approval condition must be non-empty")
        elif has_conditions:
            raise ValueError(
                f"{self.decision.value} cannot carry approval conditions"
            )
        return self


class ApprovedTermSnapshot(BaseModel):
    """The immutable, 1:1 freeze of the approved terms at decision time.

    ``snapshot_hash`` must equal ``compute_terms_hash(terms)`` -- the hash binds
    the snapshot to the exact terms it froze, so a later drift of the stored
    JSON could never masquerade as the frozen terms.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ApprovedTermSnapshotId
    decision_id: HumanCreditDecisionId
    case_id: CaseId
    case_version: int = Field(ge=1)
    terms: ApprovedTerms
    snapshot_hash: str = Field(pattern=_SHA256_HEX)

    @model_validator(mode="after")
    def _hash_matches_terms(self) -> Self:
        if self.snapshot_hash != compute_terms_hash(self.terms):
            raise ValueError("snapshot_hash must equal the canonical sha256 of terms")
        return self


def build_term_snapshot(
    *,
    snapshot_id: UUID,
    decision: HumanCreditDecision,
    terms: ApprovedTerms,
) -> ApprovedTermSnapshot:
    """Build a snapshot bound to ``decision`` with a freshly computed hash."""

    return ApprovedTermSnapshot(
        id=snapshot_id,
        decision_id=decision.id,
        case_id=decision.case_id,
        case_version=decision.case_version,
        terms=terms,
        snapshot_hash=compute_terms_hash(terms),
    )


def assert_snapshot_matches_decision(
    *,
    decision: HumanCreditDecision,
    snapshot: ApprovedTermSnapshot | None,
) -> None:
    """Enforce the decision/snapshot pairing rules; raise ``ValueError`` if wrong.

    - a decision that forbids approved terms (``DECLINED_BY_HUMAN`` /
      ``RETURNED_FOR_REVISION`` / ``MORE_INFORMATION_REQUIRED``) must have NO
      snapshot;
    - a snapshot must reference this decision and bind the same case version.

    An approval with no snapshot is valid (terms were simply not frozen).
    """

    if snapshot is None:
        return
    if decision.decision in SNAPSHOT_FORBIDDING_DECISIONS:
        raise ValueError(
            f"{decision.decision.value} cannot carry an approved-term snapshot"
        )
    if snapshot.decision_id != decision.id:
        raise ValueError("snapshot does not reference this decision")
    if (
        snapshot.case_id != decision.case_id
        or snapshot.case_version != decision.case_version
    ):
        raise ValueError("snapshot must bind the same case version as its decision")


__all__ = [
    "APPROVAL_DECISIONS",
    "SNAPSHOT_FORBIDDING_DECISIONS",
    "ApprovedTermSnapshot",
    "ApprovedTermSnapshotId",
    "ApprovedTerms",
    "CreditDecisionType",
    "HumanCreditDecision",
    "HumanCreditDecisionId",
    "assert_snapshot_matches_decision",
    "build_term_snapshot",
    "compute_terms_hash",
]
