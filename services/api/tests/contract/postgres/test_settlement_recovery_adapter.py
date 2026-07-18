"""Contract tests for the Postgres settlement / recovery adapter.

Mirrors ``tests/contract/postgres/test_conditions_adapter.py``: a fake connection
captures the exact SQL and parameters the adapter issues, proving each write is
ONE transaction (row(s) + HUMAN audit), that the settlement receipts insert is
idempotent (``on conflict ... do nothing``), and that the recovery-strategy
approval is a single select-for-update + update + audit transaction that fails
closed off ``PREPARING`` -- all without a live Postgres.  All identifiers are
synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

import pytest

from creditops.application.ports.settlement_recovery import (
    RecoveryCaseNotFound,
    RecoveryStrategyConflict,
)
from creditops.domain.settlement_recovery import (
    RecoveryCase,
    RecoveryOption,
    SettlementCheck,
    SettlementReceiptKind,
)
from creditops.infrastructure.postgres.settlement_recovery import (
    PostgresSettlementRecoveryRepository,
)

CASE = UUID("10000000-0000-0000-0000-0000000000f1")
CHECK = UUID("50000000-0000-0000-0000-0000000000f1")
RECOVERY = UUID("60000000-0000-0000-0000-0000000000f1")
CHECKER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
APPROVER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CASE_VERSION = 4
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
        self.executed_in_transaction: list[bool] = []

    def transaction(self) -> Transaction:
        return Transaction(self)

    async def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> Cursor:
        self.queries.append(query)
        self.params.append(params)
        self.executed_in_transaction.append(self.transaction_depth > 0)
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


def _repo(connection: Connection) -> PostgresSettlementRecoveryRepository:
    return PostgresSettlementRecoveryRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _option() -> RecoveryOption:
    return RecoveryOption(
        label_vi="Cơ cấu lại (mô phỏng).",
        description_vi="Đề xuất (mô phỏng).",
        consequences_vi="Hệ quả (mô phỏng).",
    )


def _recovery_row(status: str, approved_by: UUID | None = None) -> tuple[object, ...]:
    return (
        RECOVERY,
        CASE,
        CASE_VERSION,
        "Shortfall kéo dài (mô phỏng).",
        CHECKER,
        "Đề nghị chuẩn bị thu hồi (mô phỏng).",
        status,
        ["ref://ledger/exception-1"],
        [_option().model_dump(mode="json")],
        approved_by,
        NOW,
    )


# -- settlement check ---------------------------------------------------------


@pytest.mark.asyncio
async def test_record_settlement_check_is_one_transaction_with_audit() -> None:
    connection = Connection(results=[[(NOW,)], []])  # insert -> created_at; audit
    repo = _repo(connection)

    recorded = await repo.record_settlement_check(
        check=SettlementCheck(
            id=CHECK,
            case_id=CASE,
            case_version=CASE_VERSION,
            outstanding_principal="0",
            outstanding_interest="0",
            outstanding_fees="0",
            open_exception_count=0,
            zero_balance_confirmed=True,
            recorded_by=CHECKER,
        ),
        actor_id=CHECKER,
        actor_role="OPS_CHECKER",
    )

    assert recorded.id == CHECK
    assert recorded.zero_balance_confirmed is True
    assert recorded.created_at == NOW

    sql = _sql(connection)
    assert "insert into public.settlement_checks" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    audit_index = next(
        i for i, q in enumerate(connection.queries) if "audit_events" in q.lower()
    )
    assert "HUMAN:OPS_CHECKER" in (connection.params[audit_index] or ())


# -- settlement receipts (idempotent) -----------------------------------------


@pytest.mark.asyncio
async def test_record_settlement_receipts_upserts_both_kinds_and_returns_all() -> None:
    receipt_rows = [
        (UUID(int=1), CHECK, "MOCK_CLOSURE", "note-c", CHECKER, NOW),
        (UUID(int=2), CHECK, "MOCK_RELEASE", "note-r", CHECKER, NOW),
    ]
    # insert closure, insert release, audit, select-all.
    connection = Connection(results=[[], [], [], receipt_rows])
    repo = _repo(connection)

    receipts = await repo.record_settlement_receipts(
        settlement_check_id=CHECK,
        case_id=CASE,
        case_version=CASE_VERSION,
        receipts=[
            (SettlementReceiptKind.MOCK_CLOSURE, "note-c"),
            (SettlementReceiptKind.MOCK_RELEASE, "note-r"),
        ],
        actor_id=CHECKER,
        actor_role="OPS_CHECKER",
    )

    assert {r.kind for r in receipts} == {
        SettlementReceiptKind.MOCK_CLOSURE,
        SettlementReceiptKind.MOCK_RELEASE,
    }
    sql = _sql(connection)
    assert "insert into public.settlement_receipts" in sql
    assert "on conflict (settlement_check_id, kind) do nothing" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


# -- recovery case ------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_recovery_case_is_one_transaction_with_audit() -> None:
    connection = Connection(results=[[(NOW,)], []])  # insert -> created_at; audit
    repo = _repo(connection)

    recorded = await repo.record_recovery_case(
        recovery=RecoveryCase(
            id=RECOVERY,
            case_id=CASE,
            case_version=CASE_VERSION,
            trigger_summary_vi="Shortfall kéo dài (mô phỏng).",
            escalated_by=CHECKER,
            escalation_rationale_vi="Đề nghị chuẩn bị thu hồi (mô phỏng).",
            evidence_refs=("ref://ledger/exception-1",),
            options=(_option(),),
        ),
        actor_id=CHECKER,
        actor_role="OPS_CHECKER",
    )

    assert recorded.id == RECOVERY
    assert recorded.approved_by is None
    assert recorded.created_at == NOW

    sql = _sql(connection)
    assert "insert into public.recovery_cases" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


@pytest.mark.asyncio
async def test_approve_recovery_strategy_select_update_audit_one_transaction() -> None:
    # select-for-update -> PREPARING row; update/audit -> none.
    connection = Connection(results=[[_recovery_row("PREPARING")], [], []])
    repo = _repo(connection)

    approved = await repo.approve_recovery_strategy(
        recovery_id=RECOVERY,
        case_id=CASE,
        case_version=CASE_VERSION,
        approved_by=APPROVER,
        actor_role="OPS_CHECKER",
    )

    assert approved.status.value == "STRATEGY_APPROVED"
    assert approved.approved_by == APPROVER
    sql = _sql(connection)
    assert "select" in sql and "for update" in sql
    assert "update public.recovery_cases" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


@pytest.mark.asyncio
async def test_approve_recovery_strategy_conflict_off_preparing_writes_nothing() -> None:
    connection = Connection(results=[[_recovery_row("STRATEGY_APPROVED", APPROVER)]])
    repo = _repo(connection)

    with pytest.raises(RecoveryStrategyConflict):
        await repo.approve_recovery_strategy(
            recovery_id=RECOVERY,
            case_id=CASE,
            case_version=CASE_VERSION,
            approved_by=APPROVER,
            actor_role="OPS_CHECKER",
        )

    sql = _sql(connection)
    assert "update public.recovery_cases" not in sql
    assert "insert into public.audit_events" not in sql


@pytest.mark.asyncio
async def test_approve_recovery_strategy_missing_raises_not_found() -> None:
    connection = Connection(results=[[]])  # select-for-update -> no row
    repo = _repo(connection)

    with pytest.raises(RecoveryCaseNotFound):
        await repo.approve_recovery_strategy(
            recovery_id=RECOVERY,
            case_id=CASE,
            case_version=CASE_VERSION,
            approved_by=APPROVER,
            actor_role="OPS_CHECKER",
        )
