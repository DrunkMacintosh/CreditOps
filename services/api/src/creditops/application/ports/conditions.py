"""Durable-state contract for the stage-10 disbursement ConditionLedger.

Every write this port authorises is a case-scoped, version-scoped HUMAN action
(master design section 5 giai đoạn 10).  The surface is deliberately bounded:

- ``create_condition`` inserts ONE condition (bound to its source credit
  decision) plus its initial status event and an audit event, in ONE
  transaction.
- ``transition_condition`` moves ONE condition's status ALONG A VALIDATED EDGE
  only: it re-checks the domain transition map (defence in depth), writes the
  row update, the append-only status event, and the audit event in ONE
  transaction.  ``WAIVED_BY_HUMAN`` and ``NOT_APPLICABLE_BY_HUMAN`` targets
  require a rationale (the human authority record).
- ``list_conditions`` reads the ledger for an exact (case, version).
- ``list_verifying_actor_ids`` returns the distinct actors who drove a
  ``VERIFIED`` transition, so the confirmation surface can enforce that the
  independent confirming checker is none of them (separation of actor).

Nothing here confirms the gate, resolves a gap, or drives orchestration; the
``HG_DISBURSEMENT_CONDITIONS_CONFIRMED`` gate is written through the separate
``OrchestrationRepository.ensure_gate`` surface (exactly as ``api/financing.py``
records ``HG_FINANCING_NEED_CONFIRMED``), keeping the gate-writing authority out
of this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.conditions import ConditionStatus, DisbursementCondition


class ForbiddenConditionTransition(RuntimeError):
    """A ``transition_condition`` attempted an edge not in the domain map.

    Normally the application layer rejects a forbidden edge first (422); this is
    the fail-closed backstop for a lost race (the condition moved under the
    caller between the pre-check and the write) so no forbidden edge is ever
    persisted.
    """


class ConditionNotFound(RuntimeError):
    """``transition_condition`` targeted a condition absent from this (case,
    version) -- surfaced as an indistinguishable 404 at the API."""


@dataclass(frozen=True, slots=True)
class RecordedCondition:
    """Durable read model for one persisted disbursement condition."""

    id: UUID
    case_id: UUID
    case_version: int
    decision_id: UUID
    condition_text_vi: str
    owner_vi: str | None
    due_date: date | None
    status: ConditionStatus
    evidence_refs: tuple[str, ...]
    created_at: datetime


class ConditionLedgerRepository(Protocol):
    """The disbursement ConditionLedger's full durable-state surface."""

    async def create_condition(
        self,
        *,
        condition: DisbursementCondition,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedCondition: ...

    async def list_conditions(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCondition, ...]: ...

    async def load_condition(
        self, condition_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedCondition | None: ...

    async def transition_condition(
        self,
        *,
        condition_id: UUID,
        case_id: UUID,
        case_version: int,
        to_status: ConditionStatus,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str | None,
        evidence_refs: tuple[str, ...] | None,
    ) -> RecordedCondition: ...

    async def list_verifying_actor_ids(
        self, case_id: UUID, case_version: int
    ) -> frozenset[UUID]: ...

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        """Append ONE ``HUMAN:OPS_CHECKER`` audit event for the confirmation.

        Only the confirm surface uses this; the create/transition audits are
        written atomically inside ``create_condition`` / ``transition_condition``
        with the acting role.
        """
        ...
