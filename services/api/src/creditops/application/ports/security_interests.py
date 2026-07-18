"""Durable-state contract for the stage-9 per-asset security-perfection ledger.

Master design section 5 giai đoạn 9.  Every write this port authorises is a
HUMAN action recorded in ONE transaction alongside an audit row: ``create_interest``
appends a per-asset interest, ``add_item`` appends a perfection requirement to an
interest, and ``transition_item`` advances a requirement through the closed
status graph.  ``list_interests`` is the read model the confirmation gate derives
over.  Nothing here satisfies a gate or drives orchestration -- the confirm
endpoint writes ``HG_SECURITY_PERFECTION_CONFIRMED`` through the separate
``OrchestrationRepository.ensure_gate`` surface, exactly as ``api/financing.py``
records its gate (design section 16: bounded, single-purpose ports).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.security_interests import (
    PerfectionStatus,
    SecurityInterest,
    SecurityPerfectionItem,
)


class SecurityInterestError(RuntimeError):
    """Base class for the ledger's typed write failures (mapped to HTTP)."""


class InterestNotAccessibleError(SecurityInterestError):
    """An interest id is unknown or not part of the target case (fail closed)."""


class ItemNotAccessibleError(SecurityInterestError):
    """A perfection-item id is unknown or not part of the target case."""


class ForbiddenTransitionError(SecurityInterestError):
    """A status transition is not in the closed perfection transition graph."""

    def __init__(self, current: PerfectionStatus, target: PerfectionStatus) -> None:
        super().__init__(f"forbidden perfection transition {current} -> {target}")
        self.current = current
        self.target = target


class InvalidTransitionInputError(SecurityInterestError):
    """A transition is graph-valid but its inputs are incomplete.

    ``COMPLETED`` requires >=1 evidence reference; ``NOT_REQUIRED_BY_HUMAN``
    requires a non-empty rationale for the audit row.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RecordedInterest:
    """Durable read model for one persisted per-asset security interest."""

    id: UUID
    case_id: UUID
    case_version: int
    asset_description_vi: str
    asset_kind: str
    owner_name_vi: str | None
    valuation_reference: str | None
    notes_vi: str | None
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedItem:
    """Durable read model for one persisted perfection requirement."""

    id: UUID
    interest_id: UUID
    requirement_vi: str
    status: str
    evidence_refs: tuple[str, ...]
    filing_reference: str | None
    effective_date: date | None
    expiry_date: date | None
    completed_by: UUID | None
    completed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedInterestWithItems:
    """One interest and its perfection items (the confirmation read model)."""

    interest: RecordedInterest
    items: tuple[RecordedItem, ...]


class SecurityInterestRepository(Protocol):
    """The security-perfection ledger's full durable-state surface."""

    async def create_interest(
        self, *, interest: SecurityInterest, actor_role: str
    ) -> RecordedInterest: ...

    async def add_item(
        self,
        *,
        case_id: UUID,
        item: SecurityPerfectionItem,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedItem: ...

    async def transition_item(
        self,
        *,
        case_id: UUID,
        item_id: UUID,
        to_status: PerfectionStatus,
        actor_id: UUID,
        actor_role: str,
        rationale: str | None,
        evidence_refs: tuple[str, ...],
        filing_reference: str | None,
        effective_date: date | None,
        expiry_date: date | None,
    ) -> RecordedItem: ...

    async def list_interests(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedInterestWithItems, ...]: ...

    async def append_audit(
        self, event: OrchestrationAuditEvent, *, actor_id: UUID, actor_role: str
    ) -> None: ...
