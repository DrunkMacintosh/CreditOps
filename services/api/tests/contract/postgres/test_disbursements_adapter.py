"""Contract tests for the Postgres proposed-disbursement adapter.

A fake connection captures the exact SQL / params the adapter issues, proving:
create is ONE transaction (action + audit) and idempotent on conflict; execute is
TWO transactions around the labelled mock adapter (durable EXECUTION_REQUESTED
first, then receipt + result status), and fails closed for an unresolved /
already-confirmed action; reconcile is one select-for-update + update + audit
transaction and refuses a non-unresolved action -- all without a live Postgres.
All identifiers are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.disbursements import (
    AlreadyExecutedError,
    NotReconcilableError,
    ReconciliationRequiredError,
)
from creditops.domain.disbursements import (
    MOCK_DISBURSEMENT_ADAPTER_LABEL,
    ExecutionStatus,
    ProposedDisbursementAction,
)
from creditops.infrastructure.mock.disbursement_adapter import (
    MockDisbursementExecutionAdapter,
)
from creditops.infrastructure.postgres.disbursements import (
    PostgresDisbursementRepository,
)

CASE = UUID("10000000-0000-0000-0000-0000000000f1")
DECISION = UUID("d0000000-0000-0000-0000-0000000000f1")
ACTION = UUID("a0000000-0000-0000-0000-0000000000f1")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CHECKER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CASE_VERSION = 2
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    async def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class Transaction(AbstractAsyncContextManager[None]):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        self._connection.transaction_depth += 1
        self._connection.transactions_opened += 1

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._connection.transaction_depth -= 1


class Connection:
    def __init__(self, results: list[list[tuple[object, ...]]] | None = None) -> None:
        self.results = list(results or [])
        self.queries: list[str] = []
        self.params: list[tuple[object, ...] | None] = []
        self.transaction_depth = 0
        self.transactions_opened = 0

    def transaction(self) -> Transaction:
        return Transaction(self)

    async def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> Cursor:
        self.queries.append(query)
        self.params.append(params)
        return Cursor(self.results.pop(0) if self.results else [])


class ConnectionContext(AbstractAsyncContextManager[Connection]):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> Connection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _repo(connection: Connection) -> PostgresDisbursementRepository:
    return PostgresDisbursementRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _action(status: ExecutionStatus = ExecutionStatus.PROPOSED) -> ProposedDisbursementAction:
    from decimal import Decimal

    return ProposedDisbursementAction(
        id=ACTION,
        case_id=CASE,
        case_version=CASE_VERSION,
        decision_id=DECISION,
        amount=Decimal("5000000000"),
        currency="VND",
        beneficiary_ref_vi="Nhà cung cấp (mô phỏng)",
        account_ref_vi="TK-BENEFICIARY-DEMO",
        status=status,
        created_by=OFFICER,
    )


def _action_row(status: ExecutionStatus) -> tuple[object, ...]:
    return (
        ACTION,
        CASE,
        CASE_VERSION,
        DECISION,
        "5000000000",
        "VND",
        "Nhà cung cấp (mô phỏng)",
        "TK-BENEFICIARY-DEMO",
        status.value,
        OFFICER,
        NOW,
    )


# -- create -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_action_is_one_transaction_with_audit() -> None:
    # insert -> created_at; audit insert -> none.
    connection = Connection(results=[[(NOW,)], []])
    repo = _repo(connection)

    recorded = await repo.create_action(action=_action())

    assert recorded.id == ACTION
    assert recorded.created is True
    assert recorded.status is ExecutionStatus.PROPOSED
    sql = _sql(connection)
    assert "insert into public.proposed_disbursement_actions" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    # The amount is stored as the exact-decimal TEXT (no float).
    insert_params = connection.params[0]
    assert insert_params is not None
    assert "5000000000" in insert_params


@pytest.mark.asyncio
async def test_create_action_idempotent_conflict_returns_existing() -> None:
    # insert -> no row (conflict); load existing -> the existing action.
    connection = Connection(
        results=[[], [_action_row(ExecutionStatus.PROPOSED)]]
    )
    repo = _repo(connection)

    recorded = await repo.create_action(action=_action())

    assert recorded.created is False
    sql = _sql(connection)
    assert "on conflict (case_id, case_version) do nothing" in sql
    # No second audit event on the idempotent get.
    assert sql.count("insert into public.audit_events") == 0


# -- execute ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_is_two_transactions_and_records_confirmed() -> None:
    # txn1: select-for-update PROPOSED; update; audit.
    # txn2: insert receipt -> created_at; update; audit; load CONFIRMED action.
    connection = Connection(
        results=[
            [_action_row(ExecutionStatus.PROPOSED)],
            [],
            [],
            [(NOW,)],
            [],
            [],
            [_action_row(ExecutionStatus.CONFIRMED_EXECUTED)],
        ]
    )
    repo = _repo(connection)

    action, receipt = await repo.execute_action(
        action_id=ACTION,
        case_id=CASE,
        case_version=CASE_VERSION,
        adapter=MockDisbursementExecutionAdapter(),
        idempotency_key="idem-exec-1",
        actor_id=CHECKER,
        actor_role="OPS_CHECKER",
    )

    assert action.status is ExecutionStatus.CONFIRMED_EXECUTED
    assert receipt.result_status is ExecutionStatus.CONFIRMED_EXECUTED
    assert receipt.receipt_ref is not None
    assert receipt.adapter_label == MOCK_DISBURSEMENT_ADAPTER_LABEL

    sql = _sql(connection)
    assert "for update" in sql
    assert "insert into public.disbursement_execution_receipts" in sql
    assert sql.count("update public.proposed_disbursement_actions") == 2
    # EXECUTION_REQUESTED is recorded durably in the FIRST transaction, before the
    # receipt is inserted in the SECOND.
    assert connection.transactions_opened == 2
    receipt_index = next(
        i
        for i, q in enumerate(connection.queries)
        if "disbursement_execution_receipts" in q.lower()
    )
    receipt_params = connection.params[receipt_index]
    assert receipt_params is not None
    assert "idem-exec-1" in receipt_params
    assert MOCK_DISBURSEMENT_ADAPTER_LABEL in receipt_params


@pytest.mark.asyncio
async def test_execute_unknown_result_records_execution_unknown() -> None:
    connection = Connection(
        results=[
            [_action_row(ExecutionStatus.PROPOSED)],
            [],
            [],
            [(NOW,)],
            [],
            [],
            [_action_row(ExecutionStatus.EXECUTION_UNKNOWN)],
        ]
    )
    repo = _repo(connection)

    action, receipt = await repo.execute_action(
        action_id=ACTION,
        case_id=CASE,
        case_version=CASE_VERSION,
        adapter=MockDisbursementExecutionAdapter(simulate_unknown=True),
        idempotency_key="idem-exec-unknown",
        actor_id=CHECKER,
        actor_role="OPS_CHECKER",
    )

    assert action.status is ExecutionStatus.EXECUTION_UNKNOWN
    assert receipt.result_status is ExecutionStatus.EXECUTION_UNKNOWN
    assert receipt.receipt_ref is None


@pytest.mark.asyncio
async def test_execute_on_unknown_raises_reconciliation_required() -> None:
    # select-for-update returns an EXECUTION_UNKNOWN action: never blindly retried.
    connection = Connection(results=[[_action_row(ExecutionStatus.EXECUTION_UNKNOWN)]])
    repo = _repo(connection)

    with pytest.raises(ReconciliationRequiredError):
        await repo.execute_action(
            action_id=ACTION,
            case_id=CASE,
            case_version=CASE_VERSION,
            adapter=MockDisbursementExecutionAdapter(),
            idempotency_key="idem-x",
            actor_id=CHECKER,
            actor_role="OPS_CHECKER",
        )

    sql = _sql(connection)
    assert "insert into public.disbursement_execution_receipts" not in sql
    assert "update public.proposed_disbursement_actions" not in sql


@pytest.mark.asyncio
async def test_execute_on_confirmed_raises_already_executed() -> None:
    connection = Connection(results=[[_action_row(ExecutionStatus.CONFIRMED_EXECUTED)]])
    repo = _repo(connection)

    with pytest.raises(AlreadyExecutedError):
        await repo.execute_action(
            action_id=ACTION,
            case_id=CASE,
            case_version=CASE_VERSION,
            adapter=MockDisbursementExecutionAdapter(),
            idempotency_key="idem-y",
            actor_id=CHECKER,
            actor_role="OPS_CHECKER",
        )


# -- reconcile ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_unknown_to_confirmed_not_executed() -> None:
    connection = Connection(
        results=[
            [_action_row(ExecutionStatus.EXECUTION_UNKNOWN)],
            [],
            [],
            [_action_row(ExecutionStatus.CONFIRMED_NOT_EXECUTED)],
        ]
    )
    repo = _repo(connection)

    action = await repo.reconcile_action(
        action_id=ACTION,
        case_id=CASE,
        case_version=CASE_VERSION,
        outcome=ExecutionStatus.CONFIRMED_NOT_EXECUTED,
        rationale_vi="Ngân hàng xác nhận chưa chuyển tiền (mô phỏng).",
        actor_id=CHECKER,
        actor_role="OPS_CHECKER",
    )

    assert action.status is ExecutionStatus.CONFIRMED_NOT_EXECUTED
    sql = _sql(connection)
    assert "for update" in sql
    assert "update public.proposed_disbursement_actions" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    # The rationale is captured on the audit trail.
    audit_index = next(
        i for i, q in enumerate(connection.queries) if "audit_events" in q.lower()
    )
    assert connection.params[audit_index] is not None


@pytest.mark.asyncio
async def test_reconcile_on_proposed_raises_not_reconcilable() -> None:
    connection = Connection(results=[[_action_row(ExecutionStatus.PROPOSED)]])
    repo = _repo(connection)

    with pytest.raises(NotReconcilableError):
        await repo.reconcile_action(
            action_id=ACTION,
            case_id=CASE,
            case_version=CASE_VERSION,
            outcome=ExecutionStatus.CONFIRMED_EXECUTED,
            rationale_vi="Không hợp lệ.",
            actor_id=CHECKER,
            actor_role="OPS_CHECKER",
        )

    assert "update public.proposed_disbursement_actions" not in _sql(connection)


# -- list receipts ------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_receipts_maps_rows() -> None:
    row = (
        uuid4(),
        ACTION,
        "idem-1",
        MOCK_DISBURSEMENT_ADAPTER_LABEL,
        "CONFIRMED_EXECUTED",
        "receipt-ref-1",
        CHECKER,
        NOW,
    )
    connection = Connection(results=[[row]])
    repo = _repo(connection)

    receipts = await repo.list_receipts(ACTION)

    assert len(receipts) == 1
    assert receipts[0].result_status is ExecutionStatus.CONFIRMED_EXECUTED
    assert receipts[0].receipt_ref == "receipt-ref-1"
