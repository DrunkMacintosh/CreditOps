"""Per-asset security interests and their perfection-item ledger (stage 9).

Master design section 5 giai đoạn 9.  Pure, frozen value objects plus the two
deterministic derivations the confirmation gate needs; NO I/O, NO valuation, NO
priority ranking.

SPEC CONTRACT faithfully encoded here:

- ONE ``SecurityInterest`` PER ASSET -- never a single case-wide boolean.  Each
  interest carries MANY ``SecurityPerfectionItem`` requirements, tracked
  individually (evidence, filing reference, effective/expiry date, status).
- ``SecurityAssetKind`` and ``PerfectionStatus`` are PROPOSED synthetic closed
  taxonomies; they MUST be reconfigured once an official source is supplied.
- NO LLM valuation: ``valuation_reference`` is a nullable POINTER to an external
  source/adapter, never a computed value.  There is NO ``priority_rank`` field
  anywhere -- the system never declares a final priority ranking; only the
  free-text ``notes_vi`` exists.
- A ``SecurityPerfectionItem`` advances ONLY through ``ALLOWED_ITEM_TRANSITIONS``
  (``PENDING`` -> ``EVIDENCE_ATTACHED`` -> ``COMPLETED``; ``PENDING`` ->
  ``NOT_REQUIRED_BY_HUMAN``; ``COMPLETED`` -> ``EXPIRED``).  A ``COMPLETED`` item
  MUST carry at least one evidence reference and record who/when completed it.

CONFIRMABLE DERIVATION (no vacuous paths):
``derive_perfection_confirmable`` is True IFF the case has at least one interest
AND every interest has at least one item AND every item is in a terminal-
satisfied state (``COMPLETED`` or ``NOT_REQUIRED_BY_HUMAN``).  An interest with
ZERO items is NOT confirmable; a case with ZERO interests is NOT confirmable.  An
explicit no-collateral path (a case that legitimately needs no security) is
PROPOSED / out of scope here and deliberately not modelled.

All data is synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId

type SecurityInterestId = UUID
type SecurityPerfectionItemId = UUID


class SecurityAssetKind(StrEnum):
    """Closed PROPOSED synthetic set of secured-asset kinds (no official SHB
    taxonomy); reconfigure when an official source is supplied."""

    REAL_ESTATE = "REAL_ESTATE"
    VEHICLE = "VEHICLE"
    DEPOSIT = "DEPOSIT"
    RECEIVABLE = "RECEIVABLE"
    OTHER = "OTHER"


class PerfectionStatus(StrEnum):
    """Closed PROPOSED synthetic per-requirement status set.

    Advanced ONLY through ``ALLOWED_ITEM_TRANSITIONS``.  ``COMPLETED`` and
    ``NOT_REQUIRED_BY_HUMAN`` are the two terminal-satisfied states that a
    perfection requirement may rest in for the confirmation gate;
    ``EXPIRED`` is terminal but NOT satisfied (a lapsed registration reopens the
    requirement to human attention).
    """

    PENDING = "PENDING"
    EVIDENCE_ATTACHED = "EVIDENCE_ATTACHED"
    COMPLETED = "COMPLETED"
    NOT_REQUIRED_BY_HUMAN = "NOT_REQUIRED_BY_HUMAN"
    EXPIRED = "EXPIRED"


#: The ONLY permitted status transitions (mirrors the SQL trigger in
#: 202607180019_security_perfection.sql).  Every mapped value is the exact set of
#: statuses a requirement may move to from the key status; a terminal status maps
#: to the empty set.
ALLOWED_ITEM_TRANSITIONS: dict[PerfectionStatus, frozenset[PerfectionStatus]] = {
    PerfectionStatus.PENDING: frozenset(
        {PerfectionStatus.EVIDENCE_ATTACHED, PerfectionStatus.NOT_REQUIRED_BY_HUMAN}
    ),
    PerfectionStatus.EVIDENCE_ATTACHED: frozenset({PerfectionStatus.COMPLETED}),
    PerfectionStatus.COMPLETED: frozenset({PerfectionStatus.EXPIRED}),
    PerfectionStatus.NOT_REQUIRED_BY_HUMAN: frozenset(),
    PerfectionStatus.EXPIRED: frozenset(),
}

#: The terminal states in which a perfection requirement counts as SATISFIED for
#: the confirmation gate.  ``EXPIRED`` is deliberately excluded.
TERMINAL_SATISFIED_STATUSES: frozenset[PerfectionStatus] = frozenset(
    {PerfectionStatus.COMPLETED, PerfectionStatus.NOT_REQUIRED_BY_HUMAN}
)


def is_allowed_item_transition(
    current: PerfectionStatus, target: PerfectionStatus
) -> bool:
    """Whether ``current`` -> ``target`` is a permitted perfection transition."""

    return target in ALLOWED_ITEM_TRANSITIONS.get(current, frozenset())


class SecurityInterest(BaseModel):
    """One append-only security interest taken over ONE asset.

    ``valuation_reference`` is a pointer only; there is no monetary value and no
    priority ranking on this model by design.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: SecurityInterestId
    case_id: CaseId
    case_version: int = Field(ge=1)
    asset_description_vi: str = Field(min_length=1, max_length=2000)
    asset_kind: SecurityAssetKind
    owner_name_vi: str | None = Field(default=None, min_length=1, max_length=500)
    #: POINTER ONLY to an external valuation source/adapter (nullable). NEVER a
    #: computed or LLM-produced value.
    valuation_reference: str | None = Field(default=None, min_length=1, max_length=500)
    notes_vi: str | None = Field(default=None, min_length=1, max_length=4000)
    created_by: UUID


class SecurityPerfectionItem(BaseModel):
    """One perfection requirement of one interest, tracked individually.

    A ``COMPLETED`` requirement MUST carry at least one evidence reference and
    record ``completed_by`` / ``completed_at`` (mirrors the SQL CHECK).  Other
    statuses are unconstrained on those fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: SecurityPerfectionItemId
    interest_id: SecurityInterestId
    requirement_vi: str = Field(min_length=1, max_length=2000)
    status: PerfectionStatus = PerfectionStatus.PENDING
    evidence_refs: tuple[str, ...] = ()
    filing_reference: str | None = Field(default=None, min_length=1, max_length=500)
    effective_date: date | None = None
    expiry_date: date | None = None
    completed_by: UUID | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _completed_requires_evidence(self) -> Self:
        if self.status is PerfectionStatus.COMPLETED:
            if not self.evidence_refs:
                raise ValueError(
                    "a COMPLETED perfection item requires at least one evidence "
                    "reference"
                )
            if any(not ref.strip() for ref in self.evidence_refs):
                raise ValueError("every evidence reference must be non-empty")
            if self.completed_by is None or self.completed_at is None:
                raise ValueError(
                    "a COMPLETED perfection item must record completed_by and "
                    "completed_at"
                )
        return self


class SecurityInterestWithItems(BaseModel):
    """An interest paired with its perfection items (read model for derivation)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interest: SecurityInterest
    items: tuple[SecurityPerfectionItem, ...] = ()


class PerfectionBlockers(BaseModel):
    """The structured reason a case is (not yet) perfection-confirmable.

    ``blocking_item_ids`` are perfection items NOT in a terminal-satisfied state;
    ``interests_without_items`` are interests carrying zero requirements (each an
    independent blocker).  ``has_interests`` is False for a case with no
    interests at all.  ``confirmable`` is True only when every blocking channel is
    clear.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    has_interests: bool
    interests_without_items: tuple[UUID, ...] = ()
    blocking_item_ids: tuple[UUID, ...] = ()

    @property
    def confirmable(self) -> bool:
        return (
            self.has_interests
            and not self.interests_without_items
            and not self.blocking_item_ids
        )


def derive_perfection_blockers(
    interests_with_items: Sequence[SecurityInterestWithItems],
) -> PerfectionBlockers:
    """Pure structural derivation of everything blocking confirmation.

    No vacuous paths: a case with zero interests, or any interest with zero
    items, is a blocker; so is any item not in ``TERMINAL_SATISFIED_STATUSES``.
    """

    interests_without_items: list[UUID] = []
    blocking_item_ids: list[UUID] = []
    for entry in interests_with_items:
        if not entry.items:
            interests_without_items.append(entry.interest.id)
            continue
        for item in entry.items:
            if item.status not in TERMINAL_SATISFIED_STATUSES:
                blocking_item_ids.append(item.id)
    return PerfectionBlockers(
        has_interests=bool(interests_with_items),
        interests_without_items=tuple(interests_without_items),
        blocking_item_ids=tuple(blocking_item_ids),
    )


def derive_perfection_confirmable(
    interests_with_items: Sequence[SecurityInterestWithItems],
) -> bool:
    """Whether the security-perfection gate MAY be confirmed right now.

    True IFF the case has >=1 interest AND every interest has >=1 item AND every
    item is ``COMPLETED`` or ``NOT_REQUIRED_BY_HUMAN``.  Never vacuously true.
    """

    return derive_perfection_blockers(interests_with_items).confirmable


__all__ = [
    "ALLOWED_ITEM_TRANSITIONS",
    "TERMINAL_SATISFIED_STATUSES",
    "PerfectionBlockers",
    "PerfectionStatus",
    "SecurityAssetKind",
    "SecurityInterest",
    "SecurityInterestId",
    "SecurityInterestWithItems",
    "SecurityPerfectionItem",
    "SecurityPerfectionItemId",
    "derive_perfection_blockers",
    "derive_perfection_confirmable",
    "is_allowed_item_transition",
]
