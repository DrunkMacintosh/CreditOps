"""Durable state contracts for the Independent Risk Review Agent (Checker).

The checker reads maker output through a READ-ONLY surface: ``load_maker_
outputs`` returns frozen ``UnderwritingAssessment``/``LegalComplianceAssessment``
domain models plus their provenance, and this Protocol exposes NO method that
could write, update, or otherwise mutate an underwriting or legal row --
"maker-output immutability at the checker boundary".  ``tests/unit/risk_review/
test_port_surface.py`` asserts this by inspecting the Protocol's own member
set.

The only durable writes this port exposes are: an idempotent append-only
persist of the ``RiskReviewAssessment`` plus its challenges and the
checker->operations Handoff; append-only human challenge/assessment
dispositions (never a delete or edit of the challenge/assessment they attach
to); and agent audit events.  Nothing here can satisfy a gate, resolve a gap
or exception, confirm a fact, or record any credit decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.legal import LegalComplianceAssessment
from creditops.domain.risk_review import GapBlockingLevel, RiskReviewAssessment
from creditops.domain.underwriting import UnderwritingAssessment


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    """One Confirmed Fact projected into the checker's scoped evidence view."""

    confirmed_fact_id: UUID
    field_key: str
    value: str | int | float | bool
    document_version_id: UUID


@dataclass(frozen=True, slots=True)
class CheckerEvidenceView:
    """Scoped, versioned view of the Credit Case Digital Twin for the checker.

    Confirmed Facts only, exactly like the maker/reviewer evidence views --
    Candidate Facts are never authoritative input for any agent role.
    """

    case_id: UUID
    case_version: int
    built_at: datetime
    confirmed_facts: tuple[EvidenceFact, ...] = ()


@dataclass(frozen=True, slots=True)
class MakerOutputsView:
    """Both maker outputs the checker independently inspects, READ-ONLY.

    ``None`` on either side means that maker's READY_FOR_RISK_REVIEW handoff
    was not found for the current case version -- the checker use case fails
    closed rather than reviewing a partial pair (readiness already requires
    both maker handoffs; this is the defense-in-depth recheck at execution
    time).
    """

    underwriting: UnderwritingAssessment | None
    underwriting_execution_id: UUID | None
    underwriting_handoff_id: UUID | None
    legal: LegalComplianceAssessment | None
    legal_execution_id: UUID | None
    legal_handoff_id: UUID | None

    def is_complete(self) -> bool:
        return self.underwriting is not None and self.legal is not None


@dataclass(frozen=True, slots=True)
class OpenGapRecord:
    """One currently-visible (non-resolved, non-stale) evidence gap.

    The source of truth the deterministic visibility recheck compares
    declared maker gaps against (application/risk_review/analysis.py).
    """

    gap_id: UUID
    missing_information_vi: str
    blocking_level: GapBlockingLevel
    status: str


@dataclass(frozen=True, slots=True)
class ProvisionalGapRecord:
    """A PROVISIONAL evidence gap the checker itself surfaces, mirroring the
    maker/reviewer gap-record shape (application/ports/underwriting.py,
    application/ports/legal.py)."""

    issue_vi: str
    missing_information_vi: str
    blocking_level: GapBlockingLevel
    suggested_evidence_vi: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersistedCheckerOutput:
    """Durable identifiers of one persisted checker execution."""

    assessment_id: UUID
    handoff_id: UUID
    challenge_ids: tuple[UUID, ...]
    handoff_state: str
    created: bool


@dataclass(frozen=True, slots=True)
class LatestRiskReviewRecord:
    """Read model for the latest persisted checker assessment plus challenges."""

    assessment_id: UUID
    case_id: UUID
    case_version: int
    execution_id: UUID
    agent_role: str
    prompt_version: str
    created_at: datetime
    assessment: Mapping[str, object]
    handoff_id: UUID | None
    handoff_state: str | None
    handoff_created_at: datetime | None


@dataclass(frozen=True, slots=True)
class ChallengeDispositionRecord:
    """One append-only human disposition on a challenge or on the assessment.

    ``challenge_id`` is ``None`` for an assessment-level disposition (used to
    record a human NOTED disposition when the checker raised no severe
    challenge at all -- G3 must never derive SATISFIED from silence alone).
    """

    id: UUID
    assessment_id: UUID
    challenge_id: UUID | None
    disposition_type: str
    rationale_vi: str
    actor_id: UUID
    actor_role: str
    created_at: datetime


class RiskReviewRepository(Protocol):
    """The checker's full durable-state surface.  READ-ONLY for maker output."""

    async def load_evidence_view(self, case_id: UUID) -> CheckerEvidenceView | None: ...

    async def load_maker_outputs(
        self, case_id: UUID, case_version: int
    ) -> MakerOutputsView: ...

    async def load_open_gaps(
        self, case_id: UUID, case_version: int
    ) -> tuple[OpenGapRecord, ...]: ...

    async def load_latest_assessment(
        self, case_id: UUID
    ) -> LatestRiskReviewRecord | None: ...

    async def load_dispositions(
        self, case_id: UUID, case_version: int
    ) -> tuple[ChallengeDispositionRecord, ...]: ...

    async def find_persisted(
        self,
        *,
        case_id: UUID,
        case_version: int,
        task_id: UUID,
    ) -> PersistedCheckerOutput | None: ...

    async def persist_assessment(
        self,
        *,
        assessment: RiskReviewAssessment,
        handoff_id: UUID,
        handoff_state: str,
        gaps: tuple[ProvisionalGapRecord, ...],
    ) -> PersistedCheckerOutput: ...

    async def record_disposition(
        self,
        *,
        disposition_id: UUID,
        assessment_id: UUID,
        challenge_id: UUID | None,
        disposition_type: str,
        rationale_vi: str,
        actor_id: UUID,
        actor_role: str,
    ) -> ChallengeDispositionRecord: ...

    async def append_audit(self, event: OrchestrationAuditEvent) -> None: ...
