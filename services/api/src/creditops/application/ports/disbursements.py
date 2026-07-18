"""Durable-state + adapter contracts for the stage-11 proposed disbursement.

Master design section 5 giai đoạn 11.  Every write this port authorises is a
case-scoped, version-scoped HUMAN action; the execution itself runs ONLY through
the labelled mock ``DisbursementExecutionAdapter`` after BOTH human gates.  The
surface is deliberately bounded:

- ``create_action`` idempotently inserts ONE proposed disbursement action for a
  case version (a second create for the same version resolves to the existing
  action; a revision bumps the case version).  The action is derived from
  approved terms; the currency/cap validation happens in the application layer
  before this call.
- ``load_action`` / ``list_actions`` read the action(s); ``list_receipts`` reads
  an action's execution receipts.
- ``execute_action`` runs the labelled mock adapter for an action that is in a
  REATTEMPTABLE state, recording ``EXECUTION_REQUESTED`` durably BEFORE the
  adapter call and the receipt + final status AFTER, so a lost response strands
  the action in an unresolved state (never a blind retry).  It fails closed:
  ``ReconciliationRequiredError`` for an unresolved state, ``AlreadyExecutedError``
  for a confirmed one.
- ``reconcile_action`` resolves an unresolved execution to ``CONFIRMED_EXECUTED``
  or ``CONFIRMED_NOT_EXECUTED`` with a mandatory human rationale.

Nothing here satisfies a gate or drives orchestration; the two human gates
(``HG_DISBURSEMENT_VALIDATED`` / ``HG_DISBURSEMENT_AUTHORIZED``) are written
through the separate ``OrchestrationRepository.ensure_gate`` surface, keeping the
gate-writing authority out of this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from creditops.domain.disbursements import (
    DisbursementExecutionReceipt,
    ExecutionStatus,
    ProposedDisbursementAction,
)


class ReconciliationRequiredError(RuntimeError):
    """Execution was refused because the action is in an unresolved state.

    Raised by ``execute_action`` when the action is ``EXECUTION_REQUESTED`` or
    ``EXECUTION_UNKNOWN``: the prior attempt's outcome is unknown, so a human must
    reconcile it first -- it is NEVER blindly retried.  The API maps this to 409
    ``RECONCILIATION_REQUIRED``.
    """


class AlreadyExecutedError(RuntimeError):
    """The action is already ``CONFIRMED_EXECUTED`` (terminal) -- API 409."""


class NotReconcilableError(RuntimeError):
    """``reconcile_action`` targeted an action that is not in an unresolved state.

    Only ``EXECUTION_REQUESTED`` / ``EXECUTION_UNKNOWN`` may be reconciled; the API
    maps this to 409 ``NOT_RECONCILABLE``.
    """


class DisbursementActionNotFound(RuntimeError):
    """A disbursement operation targeted an action absent from this (case,
    version) -- surfaced as an indistinguishable 404 at the API."""


class DuplicateIdempotencyKeyError(RuntimeError):
    """An execution attempt reused an idempotency key already on record.

    The database enforces this (unique constraint); the adapter maps a genuine
    duplicate to this error.  In normal operation the key is generated fresh per
    attempt, so this is the fail-closed backstop against a double effect.
    """


@dataclass(frozen=True, slots=True)
class RecordedDisbursementAction:
    """Durable read model for one persisted proposed disbursement action.

    ``created`` is ``False`` when an action already existed for the (case,
    version) and was returned instead of writing a second one -- the idempotent
    record-or-get path.  ``amount_text`` is the exact-decimal money string as
    stored (no float).
    """

    id: UUID
    case_id: UUID
    case_version: int
    decision_id: UUID
    amount_text: str
    currency: str
    beneficiary_ref_vi: str
    account_ref_vi: str
    status: ExecutionStatus
    created_by: UUID
    created_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class RecordedExecutionReceipt:
    """Durable read model for one persisted execution receipt (attempt)."""

    id: UUID
    action_id: UUID
    idempotency_key: str
    adapter_label: str
    result_status: ExecutionStatus
    receipt_ref: str | None
    recorded_by: UUID
    created_at: datetime


class DisbursementExecutionAdapter(Protocol):
    """The labelled deterministic mock execution adapter contract.

    Given an action id + idempotency key it returns a deterministic receipt.  A
    constructor flag ``simulate_unknown`` (test-only, never a default) makes it
    yield an ``EXECUTION_UNKNOWN`` result to exercise the reconciliation path.  No
    real core-banking execution ever happens.
    """

    def execute(
        self, *, action_id: UUID, idempotency_key: str
    ) -> DisbursementExecutionReceipt: ...


class DisbursementRepository(Protocol):
    """The proposed disbursement's full durable-state surface."""

    async def create_action(
        self, *, action: ProposedDisbursementAction
    ) -> RecordedDisbursementAction: ...

    async def load_action(
        self, action_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedDisbursementAction | None: ...

    async def list_actions(
        self, case_id: UUID
    ) -> tuple[RecordedDisbursementAction, ...]: ...

    async def list_receipts(
        self, action_id: UUID
    ) -> tuple[RecordedExecutionReceipt, ...]: ...

    async def execute_action(
        self,
        *,
        action_id: UUID,
        case_id: UUID,
        case_version: int,
        adapter: DisbursementExecutionAdapter,
        idempotency_key: str,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedDisbursementAction, RecordedExecutionReceipt]: ...

    async def reconcile_action(
        self,
        *,
        action_id: UUID,
        case_id: UUID,
        case_version: int,
        outcome: ExecutionStatus,
        rationale_vi: str,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedDisbursementAction: ...
