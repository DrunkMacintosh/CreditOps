"""Durable-state contract for the stage-6 human credit decision.

The port exposes only what the human-only decision API needs: a read model to
validate the version + artifact bindings before recording, an idempotent
record-or-get of the decision plus its optional approved-term snapshot, and a
read of a recorded decision.  Every write this port authorises is a HUMAN
action -- ``record_decision`` writes the decision, the optional snapshot, and a
``HUMAN:CREDIT_APPROVER`` audit event in ONE transaction.  Nothing here can be
called by an agent, satisfy a gate, or drive orchestration; gate/orchestration
wiring is a later lead decision (master design section 5 stage 6).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.domain.credit_decisions import ApprovedTermSnapshot, HumanCreditDecision


@dataclass(frozen=True, slots=True)
class DecisionBinding:
    """Read model for validating a decision's version + artifact bindings.

    ``current_case_version`` is the exact version a new decision must target;
    a stale target fails closed (409) at the API.  Each ``latest_*`` id is the
    current-version artifact the decider is expected to have reviewed; the API
    rejects (422) a referenced id that is not the current one.  ``None`` on a
    ``latest_*`` id means no such artifact exists yet for the case version, so
    any referenced id for it is rejected.
    """

    current_case_version: int
    latest_memo_artifact_id: UUID | None
    latest_risk_assessment_id: UUID | None
    latest_underwriting_assessment_id: UUID | None


@dataclass(frozen=True, slots=True)
class RecordedTermSnapshot:
    """Durable read model for one persisted approved-term snapshot."""

    id: UUID
    decision_id: UUID
    case_id: UUID
    case_version: int
    terms: Mapping[str, object]
    snapshot_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedDecision:
    """Durable read model for one persisted human credit decision.

    ``created`` is ``False`` when a decision already existed for the (case,
    version) and was returned instead of writing a second one -- the idempotent
    record-or-get path.  ``snapshot`` carries the frozen approved terms when
    present.
    """

    id: UUID
    case_id: UUID
    case_version: int
    decision: str
    rationale_vi: str
    decided_by: UUID
    decided_by_role: str
    memo_artifact_id: UUID | None
    risk_assessment_id: UUID | None
    underwriting_assessment_id: UUID | None
    conditions: tuple[str, ...]
    created_at: datetime
    snapshot: RecordedTermSnapshot | None
    created: bool


class CreditDecisionRepository(Protocol):
    """The human credit decision's full durable-state surface."""

    async def load_decision_binding(self, case_id: UUID) -> DecisionBinding | None: ...

    async def load_decision(
        self, case_id: UUID, case_version: int
    ) -> RecordedDecision | None: ...

    async def record_decision(
        self,
        *,
        decision: HumanCreditDecision,
        snapshot: ApprovedTermSnapshot | None,
    ) -> RecordedDecision: ...
