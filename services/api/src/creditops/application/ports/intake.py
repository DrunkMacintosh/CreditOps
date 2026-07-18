"""Durable state contract for assigned-intake completion (master design
section 5 stage 3, section 9, section 23 -- P0 "intake completion/handoff").

The port exposes only what the ``CompleteIntake`` use case needs to turn a
fully dispositioned intake evidence state into the immutable ``IntakeHandoff``
that deterministically satisfies ``G1_INTAKE_COMPLETE`` (a
``public.handoffs`` row in state ``READY_FOR_SPECIALIST_REVIEW``).  It reads
the confirmed intake evidence back into the frozen domain models the handoff
validator (``domain/handoffs.py::_check_handoff``) requires, records the
immutable handoff idempotently, and appends a human audit event.  Nothing here
can confirm a fact, resolve a conflict/gap, satisfy a gate, or record a credit
decision -- the completeness verdict is the domain validator's alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.domain.evidence import CandidateFact, ConfirmedFact, FactConfirmation
from creditops.domain.handoffs import HandoffArtifact


@dataclass(frozen=True, slots=True)
class IntakeEvidenceView:
    """Frozen, version-scoped projection of the confirmed intake evidence.

    The tuples are exactly the inputs ``domain/handoffs.py``'s validator needs
    to decide completeness: every Candidate Fact, its single disposition
    (Fact Confirmation), the derived Confirmed Facts for supported
    dispositions, and the currently unresolved conflict/gap identifiers carried
    forward to the specialist.  Candidate Facts are never authoritative on
    their own -- the handoff is authoritative because each is confirmed.
    """

    case_id: UUID
    case_version: int
    candidates: tuple[CandidateFact, ...] = ()
    confirmations: tuple[FactConfirmation, ...] = ()
    confirmed_facts: tuple[ConfirmedFact, ...] = ()
    conflict_ids: tuple[UUID, ...] = ()
    gap_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class CurrentHandoff:
    """Read model for the current-version intake handoff (if one exists)."""

    id: UUID
    case_id: UUID
    case_version: int
    state: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PersistedHandoff:
    """Result of an idempotent handoff persist.

    ``created`` is ``False`` when a current-version handoff already existed and
    was returned instead of writing a second one -- the caller then skips the
    audit + orchestration re-tick so a repeat completion is a no-op.
    """

    handoff_id: UUID
    created: bool


@dataclass(frozen=True, slots=True)
class IntakeAuditEvent:
    """One append-only audit event for a human intake action.

    Unlike ``OrchestrationAuditEvent`` (an AGENT actor with a null actor id),
    intake completion is performed by the assigned intake officer, so the
    actor is a human whose id is recorded for provenance.
    """

    case_id: UUID
    case_version: int
    event_type: str
    actor_id: UUID
    artifact_type: str
    artifact_id: UUID
    event_data: Mapping[str, object] = field(default_factory=dict)


class IntakeRepository(Protocol):
    async def load_intake_evidence(
        self, case_id: UUID, case_version: int
    ) -> IntakeEvidenceView: ...

    async def load_current_handoff(
        self, case_id: UUID, case_version: int
    ) -> CurrentHandoff | None: ...

    async def has_current_handoff(self, case_id: UUID, case_version: int) -> bool: ...

    async def persist_handoff(
        self, handoff: HandoffArtifact, *, actor_id: UUID
    ) -> PersistedHandoff: ...

    async def append_audit(self, event: IntakeAuditEvent) -> None: ...
