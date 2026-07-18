"""Durable state contracts for the Credit Underwriting Agent (Maker).

The repository exposes exactly what the maker use case needs: a scoped
evidence view (Confirmed Facts ONLY — Candidate Facts are never authoritative
input), an idempotent append-only persist of the assessment plus its
PROVISIONAL evidence gaps and the maker->checker Handoff, and agent audit
events.  It exposes no way to satisfy a gate, resolve a gap or conflict,
confirm a fact, or record any credit decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.underwriting import GapBlockingLevel, UnderwritingAssessment


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    """One Confirmed Fact projected into the maker's scoped evidence view."""

    confirmed_fact_id: UUID
    field_key: str
    value: str | int | float | bool
    document_version_id: UUID


@dataclass(frozen=True, slots=True)
class EvidenceView:
    """Scoped, versioned view of the Credit Case Digital Twin for the maker.

    Contains Confirmed Facts only.  ``built_at`` is recorded in the assessment
    provenance so a reviewer can tell exactly which snapshot was analysed.
    """

    case_id: UUID
    case_version: int
    built_at: datetime
    confirmed_facts: tuple[EvidenceFact, ...] = ()


@dataclass(frozen=True, slots=True)
class ProvisionalGapRecord:
    """A PROVISIONAL evidence gap the maker surfaces for human consideration."""

    issue_vi: str
    missing_information_vi: str
    blocking_level: GapBlockingLevel
    suggested_evidence_vi: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersistedMakerOutput:
    """Durable identifiers of one persisted maker execution."""

    assessment_id: UUID
    handoff_id: UUID
    gap_ids: tuple[UUID, ...]
    handoff_state: str
    created: bool


@dataclass(frozen=True, slots=True)
class LatestAssessmentRecord:
    """Read model for the latest persisted assessment plus its handoff status."""

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


class UnderwritingRepository(Protocol):
    async def load_evidence_view(self, case_id: UUID) -> EvidenceView | None: ...

    async def load_latest_assessment(
        self, case_id: UUID
    ) -> LatestAssessmentRecord | None: ...

    async def find_persisted(
        self,
        *,
        case_id: UUID,
        case_version: int,
        task_id: UUID,
    ) -> PersistedMakerOutput | None: ...

    async def persist_assessment(
        self,
        *,
        assessment: UnderwritingAssessment,
        handoff_id: UUID,
        handoff_state: str,
        gaps: tuple[ProvisionalGapRecord, ...],
    ) -> PersistedMakerOutput: ...

    async def append_audit(self, event: OrchestrationAuditEvent) -> None: ...
