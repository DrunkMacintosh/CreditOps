"""Durable state contracts for the Credit Operations Agent.

The agent reads every upstream artifact through a READ-ONLY surface:
``load_upstream_view`` returns frozen ``UnderwritingAssessment`` /
``LegalComplianceAssessment`` / ``RiskReviewAssessment`` domain models plus
their provenance, and this Protocol exposes NO method that could write,
update, or otherwise mutate an intake, underwriting, legal, or risk-review
row -- "upstream-output immutability at the credit-ops boundary"
(tests/unit/credit_ops/test_port_surface.py asserts this by inspecting the
Protocol's own member set, mirroring ``application/ports/risk_review.py``).

The only durable writes this port exposes are: an idempotent append-only
persist of the ``CreditOpsPackage`` plus the operations->human-decision
Handoff; append-only human action-authorization records; append-only human
document-request approval records; and agent audit events.  Nothing here can
satisfy a gate directly, resolve a gap or challenge, confirm a fact, send a
customer communication, or execute any action -- there is no such method to
call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.credit_ops import CreditOpsPackage
from creditops.domain.legal import LegalComplianceAssessment
from creditops.domain.risk_review import RiskReviewAssessment
from creditops.domain.underwriting import GapBlockingLevel, UnderwritingAssessment


@dataclass(frozen=True, slots=True)
class CreditOpsUpstreamView:
    """Every upstream artifact the agent independently inspects, READ-ONLY.

    ``None``/``False`` on any field means that artifact was not found for the
    current case version -- the assembler still computes a deterministic
    ``package_completeness`` recording the absence; the worker processor
    additionally fails closed (defense in depth) rather than draft a memo
    with nothing to cite.
    """

    case_id: UUID
    case_version: int
    built_at: datetime
    has_intake_handoff: bool
    intake_handoff_id: UUID | None
    underwriting: UnderwritingAssessment | None
    underwriting_execution_id: UUID | None
    underwriting_handoff_id: UUID | None
    legal: LegalComplianceAssessment | None
    legal_execution_id: UUID | None
    legal_handoff_id: UUID | None
    risk_review: RiskReviewAssessment | None
    risk_review_execution_id: UUID | None
    risk_review_handoff_id: UUID | None

    def is_complete(self) -> bool:
        return (
            self.has_intake_handoff
            and self.underwriting is not None
            and self.legal is not None
            and self.risk_review is not None
        )


@dataclass(frozen=True, slots=True)
class OpenGapRecord:
    """One currently-visible (non-resolved, non-stale) evidence gap.

    Source of truth for deterministic document-request consolidation
    (application/credit_ops/analysis.py): only ``FORMAL``/``PROVISIONAL``
    gaps are consolidated into drafted document requests.
    """

    gap_id: UUID
    missing_information_vi: str
    blocking_level: GapBlockingLevel
    status: str


@dataclass(frozen=True, slots=True)
class ChallengeDispositionSummary:
    """A minimal read projection of one human disposition on a checker
    challenge, used only to derive the memo's deterministic disposition
    summary and G3/G4 status -- never to author a new disposition."""

    challenge_id: UUID | None
    disposition_type: str


@dataclass(frozen=True, slots=True)
class PersistedCreditOpsOutput:
    """Durable identifiers of one persisted credit-ops execution."""

    package_id: UUID
    handoff_id: UUID
    handoff_state: str
    created: bool


@dataclass(frozen=True, slots=True)
class LatestCreditOpsPackageRecord:
    """Read model for the latest persisted credit-ops package."""

    package_id: UUID
    case_id: UUID
    case_version: int
    execution_id: UUID
    agent_role: str
    prompt_version: str
    created_at: datetime
    package: Mapping[str, object]
    handoff_id: UUID | None
    handoff_state: str | None
    handoff_created_at: datetime | None


@dataclass(frozen=True, slots=True)
class ActionAuthorizationRecord:
    """One append-only human authorization of one proposed action.  Records
    authority only; it never executes the action it authorizes."""

    id: UUID
    package_id: UUID
    action_id: UUID
    actor_id: UUID
    actor_role: str
    rationale_vi: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentRequestApprovalRecord:
    """One append-only human approval of one drafted document request.
    Flips only the derived ``approval_status`` view; never mutates the
    package row the request lives in, and never sends anything."""

    id: UUID
    package_id: UUID
    request_id: UUID
    actor_id: UUID
    actor_role: str
    rationale_vi: str
    created_at: datetime


class CreditOpsRepository(Protocol):
    """The credit-ops agent's full durable-state surface.  READ-ONLY for
    every upstream (intake/underwriting/legal/risk-review) artifact."""

    async def load_upstream_view(self, case_id: UUID) -> CreditOpsUpstreamView | None: ...

    async def load_open_gaps(
        self, case_id: UUID, case_version: int
    ) -> tuple[OpenGapRecord, ...]: ...

    async def load_dispositions(
        self, case_id: UUID, case_version: int
    ) -> tuple[ChallengeDispositionSummary, ...]: ...

    async def load_latest_package(
        self, case_id: UUID
    ) -> LatestCreditOpsPackageRecord | None: ...

    async def load_action_authorizations(
        self, package_id: UUID
    ) -> tuple[ActionAuthorizationRecord, ...]: ...

    async def load_document_request_approvals(
        self, package_id: UUID
    ) -> tuple[DocumentRequestApprovalRecord, ...]: ...

    async def find_persisted(
        self,
        *,
        case_id: UUID,
        case_version: int,
        task_id: UUID,
    ) -> PersistedCreditOpsOutput | None: ...

    async def persist_package(
        self,
        *,
        package: CreditOpsPackage,
        handoff_id: UUID,
        handoff_state: str,
    ) -> PersistedCreditOpsOutput: ...

    async def record_action_authorization(
        self,
        *,
        authorization_id: UUID,
        package_id: UUID,
        action_id: UUID,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> ActionAuthorizationRecord: ...

    async def record_document_request_approval(
        self,
        *,
        approval_id: UUID,
        package_id: UUID,
        request_id: UUID,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> DocumentRequestApprovalRecord: ...

    async def append_audit(self, event: OrchestrationAuditEvent) -> None: ...
