"""Durable-state contract for stage-14 settlement (14A) and recovery (14B).

Every write this port authorises is a case-scoped, version-scoped HUMAN action
(master design section 5 giai đoạn 14).  The surface is deliberately bounded:

- ``record_settlement_check`` appends ONE settlement ledger check (recorded only
  when the deterministic eligibility derivation is True) plus its audit event, in
  ONE transaction.
- ``record_settlement_receipts`` appends the LABELLED MOCK closure / release
  receipts for a check plus an audit event, in ONE transaction; it is idempotent
  (a receipt kind already present is left untouched and the full set returned).
- ``record_recovery_case`` appends ONE recovery case (status ``PREPARING``) with
  its evidence pack + options plus an audit event, in ONE transaction.
- ``approve_recovery_strategy`` performs the single allowed status change
  ``PREPARING -> STRATEGY_APPROVED`` (select-for-update + update + audit, ONE
  transaction), recording the approver.  It raises ``RecoveryStrategyConflict``
  if the case is not ``PREPARING`` (already approved / lost race) and
  ``RecoveryCaseNotFound`` if the case is absent.
- the ``list_*`` / ``load_*`` reads are exact (case, version) reads.

Nothing here satisfies a gate or drives orchestration; the
``HG_SETTLEMENT_CONFIRMED`` / ``HG_RECOVERY_STRATEGY_APPROVED`` gates are written
through the separate ``OrchestrationRepository.ensure_gate`` surface (exactly as
``api/conditions.py`` records ``HG_DISBURSEMENT_CONDITIONS_CONFIRMED``), keeping
the gate-writing authority out of this repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.settlement_recovery import (
    RecoveryCase,
    RecoveryOption,
    RecoveryStatus,
    SettlementCheck,
    SettlementReceiptKind,
)


class RecoveryCaseNotFound(RuntimeError):
    """``approve_recovery_strategy`` targeted a recovery case absent from this
    (case, version) -- surfaced as an indistinguishable 404 at the API."""


class RecoveryStrategyConflict(RuntimeError):
    """``approve_recovery_strategy`` found the case not in ``PREPARING``.

    The single allowed status change is ``PREPARING -> STRATEGY_APPROVED``; a case
    that is already ``STRATEGY_APPROVED`` (or moved under the caller between the
    pre-check and the write) is rejected fail-closed, surfaced as 409.
    """


@dataclass(frozen=True, slots=True)
class RecordedSettlementCheck:
    """Durable read model for one persisted settlement ledger check."""

    id: UUID
    case_id: UUID
    case_version: int
    outstanding_principal: str
    outstanding_interest: str
    outstanding_fees: str
    open_exception_count: int
    zero_balance_confirmed: bool
    recorded_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedSettlementReceipt:
    """Durable read model for one persisted LABELLED MOCK settlement receipt."""

    id: UUID
    settlement_check_id: UUID
    kind: SettlementReceiptKind
    note_vi: str | None
    recorded_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordedRecoveryCase:
    """Durable read model for one persisted recovery case."""

    id: UUID
    case_id: UUID
    case_version: int
    trigger_summary_vi: str
    escalated_by: UUID
    escalation_rationale_vi: str
    status: RecoveryStatus
    evidence_refs: tuple[str, ...]
    options: tuple[RecoveryOption, ...]
    approved_by: UUID | None
    created_at: datetime


class SettlementRecoveryRepository(Protocol):
    """The stage-14 settlement / recovery durable-state surface."""

    # -- settlement (14A) -----------------------------------------------------

    async def record_settlement_check(
        self,
        *,
        check: SettlementCheck,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedSettlementCheck: ...

    async def list_settlement_checks(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedSettlementCheck, ...]: ...

    async def load_latest_settlement_check(
        self, case_id: UUID, case_version: int
    ) -> RecordedSettlementCheck | None: ...

    async def record_settlement_receipts(
        self,
        *,
        settlement_check_id: UUID,
        case_id: UUID,
        case_version: int,
        receipts: Sequence[tuple[SettlementReceiptKind, str | None]],
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedSettlementReceipt, ...]: ...

    async def list_settlement_receipts(
        self, settlement_check_id: UUID
    ) -> tuple[RecordedSettlementReceipt, ...]: ...

    # -- recovery (14B) -------------------------------------------------------

    async def record_recovery_case(
        self,
        *,
        recovery: RecoveryCase,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedRecoveryCase: ...

    async def list_recovery_cases(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedRecoveryCase, ...]: ...

    async def load_recovery_case(
        self, recovery_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedRecoveryCase | None: ...

    async def approve_recovery_strategy(
        self,
        *,
        recovery_id: UUID,
        case_id: UUID,
        case_version: int,
        approved_by: UUID,
        actor_role: str,
    ) -> RecordedRecoveryCase: ...

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        """Append ONE ``HUMAN:OPS_CHECKER`` audit event for a gate confirmation.

        Used only by the settlement-confirm / recovery-approve surfaces; the
        record_* audits are written atomically inside their write methods with
        the acting role.
        """
        ...
