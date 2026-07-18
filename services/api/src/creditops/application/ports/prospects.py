"""Durable state contract for the Stage 1 prospect store (master design
section 5 stage 1, section 13).

The port exposes only what the prospect API needs, and every method is scoped
by ``created_by`` (the owning intake officer) -- there is no cross-officer
read.  Nothing here can compute a verdict or trigger a contact: it creates a
prospect, lists an officer's own prospects (keyset-paginated), loads one
prospect with its latest screening snapshot and its contact decisions, appends
a descriptive screening snapshot (auto-versioned), and records the human's
contact decision (an append-only durable record, no side effect).

An operation that targets a prospect the actor does not own raises
``ProspectNotFound`` so the API can return an indistinguishable 404 -- ownership
is never disclosed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from creditops.domain.prospects import (
    ContactDecision,
    ContactDecisionRecord,
    Prospect,
    ScreeningSnapshot,
)


class ProspectNotFound(RuntimeError):
    """The prospect does not exist, or is not owned by this actor.

    The two cases are intentionally indistinguishable so the API never
    discloses whether a prospect it cannot show actually exists.
    """


@dataclass(frozen=True, slots=True)
class ProspectDetail:
    """One prospect with its latest screening snapshot and contact decisions.

    ``latest_snapshot`` is ``None`` when no screening snapshot has been
    appended yet.  ``decisions`` is the full append-only history, oldest first.
    """

    prospect: Prospect
    latest_snapshot: ScreeningSnapshot | None = None
    decisions: tuple[ContactDecisionRecord, ...] = field(default_factory=tuple)


class ProspectRepository(Protocol):
    async def create_prospect(
        self,
        *,
        name_vi: str,
        industry_vi: str | None,
        years_operating: int | None,
        revenue_band_vi: str | None,
        legal_status_vi: str | None,
        notes_vi: str | None,
        created_by: UUID,
    ) -> Prospect: ...

    async def list_prospects(
        self, *, created_by: UUID, cursor: UUID | None, limit: int
    ) -> tuple[tuple[Prospect, ...], UUID | None]: ...

    async def load_prospect(
        self, *, prospect_id: UUID, created_by: UUID
    ) -> ProspectDetail | None: ...

    async def append_screening_snapshot(
        self,
        *,
        prospect_id: UUID,
        created_by: UUID,
        screening_config_version: str,
        industry_vi: str | None,
        years_operating: int | None,
        revenue_band_vi: str | None,
        legal_status_vi: str | None,
        credit_history_vi: str | None,
        risk_appetite_note_vi: str | None,
        details: Mapping[str, object],
    ) -> ScreeningSnapshot: ...

    async def record_contact_decision(
        self,
        *,
        prospect_id: UUID,
        created_by: UUID,
        decision: ContactDecision,
        rationale_vi: str,
        decided_by: UUID,
    ) -> ContactDecisionRecord: ...


__all__ = [
    "ProspectDetail",
    "ProspectNotFound",
    "ProspectRepository",
]
