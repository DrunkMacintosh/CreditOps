"""Durable Postgres adapter for the stage-11 proposed disbursement.

Every write is human-only and case/version scoped, and each is a single
transaction that writes the domain row plus a ``HUMAN:<role>`` audit event.

- ``create_action`` idempotently inserts the action on the ``(case_id,
  case_version)`` unique key; a duplicate resolves to the existing action
  (``created=False``) and writes NO second audit event.
- ``execute_action`` records ``EXECUTION_REQUESTED`` durably in its OWN
  transaction BEFORE invoking the labelled mock adapter, then, in a SECOND
  transaction, inserts the append-only receipt (unique idempotency key) and moves
  the action to the adapter's result status.  A lost response between the two
  strands the action in ``EXECUTION_REQUESTED`` (reconcilable, never a blind
  retry).  It fails closed for an unresolved or already-confirmed action.
- ``reconcile_action`` moves an unresolved action to ``CONFIRMED_EXECUTED`` /
  ``CONFIRMED_NOT_EXECUTED`` with a mandatory human rationale (captured in the
  audit event).

Nothing here satisfies a gate or drives orchestration.  All identifiers and data
are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.disbursements import (
    AlreadyExecutedError,
    DisbursementActionNotFound,
    DisbursementExecutionAdapter,
    NotReconcilableError,
    ReconciliationRequiredError,
    RecordedDisbursementAction,
    RecordedExecutionReceipt,
)
from creditops.domain.disbursements import (
    REATTEMPTABLE_STATUSES,
    RECONCILABLE_STATUSES,
    RECONCILIATION_OUTCOMES,
    DisbursementExecutionReceipt,
    ExecutionStatus,
    ProposedDisbursementAction,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_ARTIFACT_TYPE = "PROPOSED_DISBURSEMENT_ACTION"
_CREATED_EVENT_TYPE = "PROPOSED_DISBURSEMENT_CREATED"
_REQUESTED_EVENT_TYPE = "DISBURSEMENT_EXECUTION_REQUESTED"
_RECORDED_EVENT_TYPE = "DISBURSEMENT_EXECUTION_RECORDED"
_RECONCILED_EVENT_TYPE = "DISBURSEMENT_EXECUTION_RECONCILED"


class PostgresDisbursementRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- create ---------------------------------------------------------------

    async def create_action(
        self, *, action: ProposedDisbursementAction
    ) -> RecordedDisbursementAction:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.proposed_disbursement_actions (
                      id, case_id, case_version, decision_id, amount_text,
                      currency, beneficiary_ref_vi, account_ref_vi, status,
                      created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (case_id, case_version) do nothing
                    returning created_at
                    """,
                    (
                        action.id,
                        action.case_id,
                        action.case_version,
                        action.decision_id,
                        action.amount_text,
                        action.currency,
                        action.beneficiary_ref_vi,
                        action.account_ref_vi,
                        action.status.value,
                        action.created_by,
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    existing = await self._load_action_for_version(
                        connection, action.case_id, action.case_version
                    )
                    if existing is None:
                        raise RuntimeError(
                            "proposed disbursement idempotency row vanished"
                        )
                    return existing
                await self._insert_audit(
                    connection,
                    case_id=action.case_id,
                    case_version=action.case_version,
                    event_type=_CREATED_EVENT_TYPE,
                    actor_id=action.created_by,
                    actor_role="OPS_OFFICER",
                    artifact_id=action.id,
                    event_data={
                        "decisionId": str(action.decision_id),
                        "amount": action.amount_text,
                        "currency": action.currency,
                        "status": action.status.value,
                    },
                )
                created_at = cast(datetime, inserted[0])
        return RecordedDisbursementAction(
            id=action.id,
            case_id=action.case_id,
            case_version=action.case_version,
            decision_id=action.decision_id,
            amount_text=action.amount_text,
            currency=action.currency,
            beneficiary_ref_vi=action.beneficiary_ref_vi,
            account_ref_vi=action.account_ref_vi,
            status=action.status,
            created_by=action.created_by,
            created_at=created_at,
            created=True,
        )

    # -- reads ----------------------------------------------------------------

    async def load_action(
        self, action_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedDisbursementAction | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                f"""
                select {_ACTION_COLUMNS}
                from public.proposed_disbursement_actions
                where id = %s and case_id = %s and case_version = %s
                """,
                (action_id, case_id, case_version),
            )
            row = await cursor.fetchone()
        return _row_to_action(row) if row is not None else None

    async def list_actions(
        self, case_id: UUID
    ) -> tuple[RecordedDisbursementAction, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                f"""
                select {_ACTION_COLUMNS}
                from public.proposed_disbursement_actions
                where case_id = %s
                order by case_version desc, created_at desc
                """,
                (case_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_action(row) for row in rows)

    async def list_receipts(
        self, action_id: UUID
    ) -> tuple[RecordedExecutionReceipt, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                f"""
                select {_RECEIPT_COLUMNS}
                from public.disbursement_execution_receipts
                where action_id = %s
                order by created_at asc, id asc
                """,
                (action_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_receipt(row) for row in rows)

    # -- execute (two transactions around the mock adapter) -------------------

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
    ) -> tuple[RecordedDisbursementAction, RecordedExecutionReceipt]:
        # Transaction 1: lock the action, fail closed for an unresolved / confirmed
        # state, and durably record EXECUTION_REQUESTED BEFORE any adapter call.
        async with self._connection_factory() as connection:
            async with connection.transaction():
                current = await self._select_for_update(
                    connection, action_id, case_id, case_version
                )
                if current.status in RECONCILABLE_STATUSES:
                    # An unresolved prior attempt: NEVER blindly retried.
                    raise ReconciliationRequiredError(current.status.value)
                if current.status is ExecutionStatus.CONFIRMED_EXECUTED:
                    raise AlreadyExecutedError(str(action_id))
                if current.status not in REATTEMPTABLE_STATUSES:  # defensive
                    raise ReconciliationRequiredError(current.status.value)
                await self._update_status(
                    connection,
                    action_id=action_id,
                    case_id=case_id,
                    case_version=case_version,
                    to_status=ExecutionStatus.EXECUTION_REQUESTED,
                )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_REQUESTED_EVENT_TYPE,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_id=action_id,
                    event_data={
                        "idempotencyKey": idempotency_key,
                        "fromStatus": current.status.value,
                    },
                )

        # The labelled mock adapter runs OUTSIDE the transaction: a lost response
        # here leaves the action durably in EXECUTION_REQUESTED (reconcilable).
        receipt: DisbursementExecutionReceipt = adapter.execute(
            action_id=action_id, idempotency_key=idempotency_key
        )

        # Transaction 2: persist the append-only receipt (unique idempotency key)
        # and move the action to the adapter's result status.
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.disbursement_execution_receipts (
                      id, action_id, idempotency_key, adapter_label,
                      result_status, receipt_ref, recorded_by
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        receipt.id,
                        receipt.action_id,
                        receipt.idempotency_key,
                        receipt.adapter_label,
                        receipt.result_status.value,
                        receipt.receipt_ref,
                        actor_id,
                    ),
                )
                inserted = await cursor.fetchone()
                receipt_created_at = (
                    cast(datetime, inserted[0]) if inserted is not None else None
                )
                await self._update_status(
                    connection,
                    action_id=action_id,
                    case_id=case_id,
                    case_version=case_version,
                    to_status=receipt.result_status,
                )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_RECORDED_EVENT_TYPE,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_id=action_id,
                    event_data={
                        "idempotencyKey": receipt.idempotency_key,
                        "adapterLabel": receipt.adapter_label,
                        "resultStatus": receipt.result_status.value,
                        "receiptRef": receipt.receipt_ref,
                    },
                )
                action = await self._load_action_for_version(
                    connection, case_id, case_version
                )
        if action is None:  # pragma: no cover - the row was just updated
            raise DisbursementActionNotFound(str(action_id))
        recorded_receipt = RecordedExecutionReceipt(
            id=receipt.id,
            action_id=receipt.action_id,
            idempotency_key=receipt.idempotency_key,
            adapter_label=receipt.adapter_label,
            result_status=receipt.result_status,
            receipt_ref=receipt.receipt_ref,
            recorded_by=actor_id,
            created_at=cast(datetime, receipt_created_at),
        )
        return action, recorded_receipt

    # -- reconcile ------------------------------------------------------------

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
    ) -> RecordedDisbursementAction:
        if outcome not in RECONCILIATION_OUTCOMES:  # defensive
            raise ValueError(f"{outcome.value} is not a reconciliation outcome")
        async with self._connection_factory() as connection:
            async with connection.transaction():
                current = await self._select_for_update(
                    connection, action_id, case_id, case_version
                )
                if current.status not in RECONCILABLE_STATUSES:
                    raise NotReconcilableError(current.status.value)
                await self._update_status(
                    connection,
                    action_id=action_id,
                    case_id=case_id,
                    case_version=case_version,
                    to_status=outcome,
                )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_RECONCILED_EVENT_TYPE,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_id=action_id,
                    event_data={
                        "fromStatus": current.status.value,
                        "outcome": outcome.value,
                        "rationale": rationale_vi,
                    },
                )
                action = await self._load_action_for_version(
                    connection, case_id, case_version
                )
        if action is None:  # pragma: no cover - the row was just updated
            raise DisbursementActionNotFound(str(action_id))
        return action

    # -- helpers --------------------------------------------------------------

    @staticmethod
    async def _select_for_update(
        connection: DatabaseConnection,
        action_id: UUID,
        case_id: UUID,
        case_version: int,
    ) -> RecordedDisbursementAction:
        cursor = await connection.execute(
            f"""
            select {_ACTION_COLUMNS}
            from public.proposed_disbursement_actions
            where id = %s and case_id = %s and case_version = %s
            for update
            """,
            (action_id, case_id, case_version),
        )
        row = await cursor.fetchone()
        if row is None:
            raise DisbursementActionNotFound(str(action_id))
        return _row_to_action(row)

    @staticmethod
    async def _update_status(
        connection: DatabaseConnection,
        *,
        action_id: UUID,
        case_id: UUID,
        case_version: int,
        to_status: ExecutionStatus,
    ) -> None:
        await connection.execute(
            """
            update public.proposed_disbursement_actions
            set status = %s
            where id = %s and case_id = %s and case_version = %s
            """,
            (to_status.value, action_id, case_id, case_version),
        )

    @staticmethod
    async def _load_action_for_version(
        connection: DatabaseConnection, case_id: UUID, case_version: int
    ) -> RecordedDisbursementAction | None:
        cursor = await connection.execute(
            f"""
            select {_ACTION_COLUMNS}
            from public.proposed_disbursement_actions
            where case_id = %s and case_version = %s
            """,
            (case_id, case_version),
        )
        row = await cursor.fetchone()
        return _row_to_action(row) if row is not None else None

    @staticmethod
    async def _insert_audit(
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        event_type: str,
        actor_id: UUID,
        actor_role: str,
        artifact_id: UUID,
        event_data: dict[str, object],
    ) -> None:
        await connection.execute(
            """
            insert into public.audit_events (
              case_id, case_version, event_type, actor_type, actor_id,
              artifact_type, artifact_id, event_data
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                case_id,
                case_version,
                event_type,
                f"HUMAN:{actor_role}",
                actor_id,
                _ARTIFACT_TYPE,
                artifact_id,
                Jsonb(event_data),
            ),
        )


_ACTION_COLUMNS = (
    "id, case_id, case_version, decision_id, amount_text, currency, "
    "beneficiary_ref_vi, account_ref_vi, status, created_by, created_at"
)

_RECEIPT_COLUMNS = (
    "id, action_id, idempotency_key, adapter_label, result_status, receipt_ref, "
    "recorded_by, created_at"
)


def _row_to_action(row: Sequence[Any]) -> RecordedDisbursementAction:
    return RecordedDisbursementAction(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        decision_id=cast(UUID, row[3]),
        amount_text=str(row[4]),
        currency=str(row[5]),
        beneficiary_ref_vi=str(row[6]),
        account_ref_vi=str(row[7]),
        status=ExecutionStatus(str(row[8])),
        created_by=cast(UUID, row[9]),
        created_at=cast(datetime, row[10]),
        created=False,
    )


def _row_to_receipt(row: Sequence[Any]) -> RecordedExecutionReceipt:
    return RecordedExecutionReceipt(
        id=cast(UUID, row[0]),
        action_id=cast(UUID, row[1]),
        idempotency_key=str(row[2]),
        adapter_label=str(row[3]),
        result_status=ExecutionStatus(str(row[4])),
        receipt_ref=cast("str | None", row[5]),
        recorded_by=cast(UUID, row[6]),
        created_at=cast(datetime, row[7]),
    )
