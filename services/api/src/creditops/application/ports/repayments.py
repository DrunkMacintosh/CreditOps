"""Durable-state contract for the stage-13 RepaymentLedger.

The ledger STATE is never persisted -- it is recomputed by
``domain/repayments.py::apply_events``.  This port therefore only stores the two
append-only source-of-truth tables plus the human free-text collection notes:

- ``create_facility`` inserts ONE disbursed facility (bound to its source
  permitting credit decision) plus a ``HUMAN:<role>`` audit event, in ONE
  transaction.  The facility is immutable thereafter.
- ``record_event`` appends ONE payment / reversal.  It is IDEMPOTENT on
  ``(facility_id, external_reference)``: a duplicate delivery returns the EXISTING
  row (``created=False``) and writes no second economic effect.
- ``list_events`` reads the full append-only history for a facility.
- ``load_facility`` / ``list_facilities`` read facilities for an exact (case,
  version).
- ``record_collection_note`` appends ONE human free-text observation / proposed
  action.  It is a PROPOSAL only -- nothing here executes any control.
- ``list_collection_notes`` reads the notes for a facility.

Nothing here confirms a gate, resolves a gap, drives orchestration, or executes a
proposed action.  Reversal, partial, late, out-of-order and backdated handling is
entirely in the deterministic fold; this port just stores immutable facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, cast
from uuid import UUID

from creditops.application.underwriting.calculators import RepaymentStyle
from creditops.domain.repayments import EventKind, Facility, RepaymentEvent


class FacilityNotFound(RuntimeError):
    """A repayment event or note targeted a facility absent from this (case,
    version) -- surfaced as an indistinguishable 404 at the API."""


@dataclass(frozen=True, slots=True)
class RecordedFacility:
    """Durable read model for one persisted disbursed facility."""

    id: UUID
    case_id: UUID
    case_version: int
    decision_id: UUID
    principal: Decimal
    annual_rate_percent: Decimal
    term_months: int
    periodic_fee: Decimal
    repayment_style: str
    first_payment_date: date
    created_at: datetime

    def to_facility(self) -> Facility:
        """Rebuild the immutable domain ``Facility`` for the deterministic fold."""
        return Facility(
            id=self.id,
            case_id=self.case_id,
            case_version=self.case_version,
            decision_id=self.decision_id,
            principal=self.principal,
            annual_rate_percent=self.annual_rate_percent,
            term_months=self.term_months,
            repayment_style=cast(RepaymentStyle, self.repayment_style),
            first_payment_date=self.first_payment_date,
            periodic_fee=self.periodic_fee,
            created_at=self.created_at,
        )


@dataclass(frozen=True, slots=True)
class RecordedRepaymentEvent:
    """Durable read model for one persisted repayment event."""

    id: UUID
    facility_id: UUID
    kind: str
    amount: Decimal
    external_reference: str
    reversed_event_id: UUID | None
    effective_date: date
    recorded_at: datetime

    def to_event(self) -> RepaymentEvent:
        return RepaymentEvent(
            id=self.id,
            facility_id=self.facility_id,
            kind=EventKind(self.kind),
            amount=self.amount,
            external_reference=self.external_reference,
            reversed_event_id=self.reversed_event_id,
            effective_date=self.effective_date,
            recorded_at=self.recorded_at,
        )


@dataclass(frozen=True, slots=True)
class RecordedCollectionNote:
    """Durable read model for one persisted human collection note."""

    id: UUID
    facility_id: UUID
    case_id: UUID
    case_version: int
    note_kind: str
    note_text_vi: str
    proposed_action_vi: str | None
    author_id: UUID
    author_role: str
    created_at: datetime


class RepaymentLedgerRepository(Protocol):
    """The RepaymentLedger's full durable-state surface (append-only facts)."""

    async def create_facility(
        self, *, facility: Facility, actor_id: UUID, actor_role: str
    ) -> RecordedFacility: ...

    async def load_facility(
        self, facility_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedFacility | None: ...

    async def list_facilities(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedFacility, ...]: ...

    async def record_event(
        self, *, event: RepaymentEvent, actor_id: UUID, actor_role: str
    ) -> tuple[RecordedRepaymentEvent, bool]:
        """Append ONE event idempotently.  Returns ``(row, created)``: on a
        duplicate ``external_reference`` the existing row is returned with
        ``created=False`` and no second effect is written."""
        ...

    async def list_events(
        self, facility_id: UUID
    ) -> tuple[RecordedRepaymentEvent, ...]: ...

    async def record_collection_note(
        self,
        *,
        facility_id: UUID,
        case_id: UUID,
        case_version: int,
        note_kind: str,
        note_text_vi: str,
        proposed_action_vi: str | None,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedCollectionNote: ...

    async def list_collection_notes(
        self, facility_id: UUID
    ) -> tuple[RecordedCollectionNote, ...]: ...
