"""Durable-state contract for the versioned FinancingRequest (stage 2).

This port is deliberately FINANCING-ONLY: it can read the version history, read
the latest version, append a new version, and append a case audit event.  It
exposes NO way to satisfy a gate, record a credit decision, resolve a gap, or
confirm a fact.

The stage-2 confirmation gate (``HG_FINANCING_NEED_CONFIRMED``) is NOT written
through this port.  The confirm endpoint writes it through the separate
``OrchestrationRepository.ensure_gate`` surface, exactly as ``api/risk_review.py``
records G3 -- keeping the gate-writing authority out of the financing repository
(master design section 16: bounded, single-purpose ports).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.financing_requests import (
    FinancingRequestDraft,
    FinancingRequestVersion,
)


class FinancingRepository(Protocol):
    async def list_versions(
        self, case_id: UUID
    ) -> tuple[FinancingRequestVersion, ...]: ...

    async def latest_version(
        self, case_id: UUID
    ) -> FinancingRequestVersion | None: ...

    async def append_version(
        self,
        *,
        case_id: UUID,
        case_version: int,
        fields: FinancingRequestDraft,
        actor_id: UUID,
    ) -> FinancingRequestVersion: ...

    async def append_audit(self, event: OrchestrationAuditEvent) -> None: ...
