"""Durable-state contract for the stage-12 post-credit monitoring surfaces.

Master design section 5 giai đoạn 12.  Every write is a case-scoped, version-scoped
HUMAN action recorded with the acting role; the agent role never writes here.  The
surface is deliberately bounded and each write is ONE transaction:

- ``create_obligations`` persists a deterministically generated run of monitoring
  obligations (append-only) plus one audit event.
- ``record_observation`` persists ONE append-only longitudinal observation (with
  its separated ``effective_at`` / ``observed_at`` caller timestamps and the DB
  ``recorded_at`` clock) and, when the deterministic OVERDUE_OBLIGATION rule fired,
  the ``OPEN`` early-warning alert it raised -- both in the SAME transaction, with
  per-obligation dedup so a second late observation never re-raises the alert.
- ``create_covenant`` persists ONE append-only covenant carrying its declared
  (versioned) threshold plus an audit event.
- ``record_covenant_test`` persists ONE append-only covenant test (the exact
  echoed arithmetic + verdict) and, when the deterministic COVENANT_BREACH rule
  fired, the ``OPEN`` early-warning alert it raised -- both in the SAME
  transaction, deduped per covenant test.
- ``dispose_alert`` moves ONE alert along a VALIDATED lifecycle edge (re-checking
  the domain map, defence in depth) and appends its mandatory-rationale
  disposition row + an audit event, in ONE transaction.
- the ``list_*`` / ``load_*`` reads surface an exact (case, version).

Nothing here confirms a gate or drives orchestration: stage 12 adds NO gate -- the
human alert disposition IS the control.  There is deliberately NO debt
classification anywhere on this surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from creditops.domain.monitoring import (
    AlertRule,
    AlertStatus,
    ComparisonOperator,
    Covenant,
    CovenantEvaluation,
    EarlyWarningAlert,
    GeneratedObligation,
    MonitoringObservation,
    ObligationFrequency,
    ObligationSpec,
)


class AlertNotFound(RuntimeError):
    """``dispose_alert`` targeted an alert absent from this (case, version) --
    surfaced as an indistinguishable 404 at the API."""


class ForbiddenAlertTransition(RuntimeError):
    """A ``dispose_alert`` attempted an edge not in the domain lifecycle map.

    Normally the application layer rejects a forbidden edge first (422); this is
    the fail-closed backstop for a lost race (the alert moved under the caller
    between the pre-check and the write) so no forbidden edge is ever persisted.
    """


@dataclass(frozen=True, slots=True)
class RecordedObligation:
    """Durable read model for one persisted monitoring obligation."""

    id: UUID
    case_id: UUID
    case_version: int
    sequence: int
    frequency: ObligationFrequency
    due_date: date
    requirement_text_vi: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedObservation:
    """Durable read model for one persisted monitoring observation.

    ``effective_at`` / ``observed_at`` are the caller's separated timestamps;
    ``recorded_at`` is the database clock (the trusted persistence time).
    """

    id: UUID
    case_id: UUID
    case_version: int
    obligation_id: UUID | None
    observation_type_vi: str
    body_vi: str
    effective_at: datetime
    observed_at: datetime
    recorded_at: datetime
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecordedCovenant:
    """Durable read model for one persisted covenant + its declared threshold."""

    id: UUID
    case_id: UUID
    case_version: int
    name_vi: str
    metric_key: str
    operator: ComparisonOperator
    threshold_value: Decimal
    threshold_version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedCovenantTest:
    """Durable read model for one persisted covenant test (echoed arithmetic)."""

    id: UUID
    covenant_id: UUID
    case_id: UUID
    case_version: int
    metric_key: str
    operator: ComparisonOperator
    numerator: Decimal
    denominator: Decimal
    threshold_value: Decimal
    threshold_version: int
    comparison_lhs: Decimal
    comparison_rhs: Decimal
    passed: bool
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedAlert:
    """Durable read model for one persisted early-warning alert."""

    id: UUID
    case_id: UUID
    case_version: int
    rule: AlertRule
    status: AlertStatus
    detail_vi: str
    source_covenant_test_id: UUID | None
    source_obligation_id: UUID | None
    source_observation_id: UUID | None
    created_at: datetime


class MonitoringRepository(Protocol):
    """The stage-12 post-credit monitoring durable-state surface."""

    async def create_obligations(
        self,
        *,
        case_id: UUID,
        case_version: int,
        spec: ObligationSpec,
        obligations: tuple[GeneratedObligation, ...],
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedObligation, ...]: ...

    async def list_obligations(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedObligation, ...]: ...

    async def load_obligation(
        self, obligation_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedObligation | None: ...

    async def record_observation(
        self,
        *,
        observation: MonitoringObservation,
        overdue_alert: EarlyWarningAlert | None,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedObservation, RecordedAlert | None]: ...

    async def list_observations(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedObservation, ...]: ...

    async def create_covenant(
        self, *, covenant: Covenant, actor_id: UUID, actor_role: str
    ) -> RecordedCovenant: ...

    async def list_covenants(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCovenant, ...]: ...

    async def load_covenant(
        self, covenant_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedCovenant | None: ...

    async def record_covenant_test(
        self,
        *,
        test_id: UUID,
        covenant_id: UUID,
        case_id: UUID,
        case_version: int,
        evaluation: CovenantEvaluation,
        breach_alert: EarlyWarningAlert | None,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedCovenantTest, RecordedAlert | None]: ...

    async def list_covenant_tests(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCovenantTest, ...]: ...

    async def list_alerts(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedAlert, ...]: ...

    async def load_alert(
        self, alert_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedAlert | None: ...

    async def dispose_alert(
        self,
        *,
        alert_id: UUID,
        case_id: UUID,
        case_version: int,
        to_status: AlertStatus,
        rationale_vi: str,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedAlert: ...
