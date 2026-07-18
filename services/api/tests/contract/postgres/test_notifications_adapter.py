"""Contract tests for the Postgres notification persistence adapter.

Mirrors ``tests/contract/postgres/test_credit_decisions_adapter.py``: a fake
connection captures the exact SQL and parameters, proving the single-transaction
writes (draft + ``HUMAN:OPS_OFFICER`` audit; receipt + audit), the on-conflict
idempotency (no second audit on a repeat), the permitting-decision precondition,
and the gate re-assertion on delivery -- all without a live Postgres.  All
identifiers are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.notifications import (
    DecisionDoesNotPermitNotificationError,
    GateNotSatisfiedError,
)
from creditops.domain.notifications import MOCK_DELIVERY_CHANNEL, compute_content_hash
from creditops.infrastructure.postgres.notifications import (
    PostgresNotificationRepository,
)

CASE = UUID("10000000-0000-0000-0000-0000000000f7")
DECISION_ID = UUID("d0000000-0000-0000-0000-0000000000f7")
DRAFT_ID = UUID("c0000000-0000-0000-0000-0000000000f7")
RECEIPT_ID = UUID("e0000000-0000-0000-0000-0000000000f7")
DECIDER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CREATOR = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
DELIVERER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CASE_VERSION = 1
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    async def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None


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


def _repo(connection: Connection) -> PostgresNotificationRepository:
    return PostgresNotificationRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _decision_row(
    *, decision: str = "APPROVED_AS_PROPOSED", conditions: list[str] | None = None
) -> tuple[object, ...]:
    return (
        DECISION_ID,
        decision,
        "Đã phê duyệt (dữ liệu mô phỏng).",
        DECIDER,
        "CREDIT_APPROVER",
        conditions or [],
        None,
        None,
        None,
    )


def _draft_row(content_vi: str) -> tuple[object, ...]:
    return (
        DRAFT_ID,
        CASE,
        CASE_VERSION,
        DECISION_ID,
        content_vi,
        compute_content_hash(content_vi),
        CREATOR,
        NOW,
    )


@pytest.mark.asyncio
async def test_create_draft_is_one_transaction_with_audit() -> None:
    connection = Connection(
        results=[
            [(CASE_VERSION,)],  # current case version
            [_decision_row()],  # permitting decision
            [],  # no approved-term snapshot
            [(DRAFT_ID, NOW)],  # draft insert -> inserted
            [],  # audit insert
        ]
    )
    repo = _repo(connection)

    recorded = await repo.create_draft(
        draft_id=DRAFT_ID, case_id=CASE, created_by=CREATOR
    )

    assert recorded.created is True
    assert recorded.content_hash == compute_content_hash(recorded.content_vi)
    sql = _sql(connection)
    assert "insert into public.credit_notification_drafts" in sql
    assert "on conflict (case_id, case_version) do nothing" in sql
    assert "insert into public.audit_events" in sql
    # The draft insert + audit run inside exactly one transaction; the read-model
    # loads (version/decision/terms) run before it.
    assert connection.transactions_opened == 1
    write_flags = [
        flag
        for flag, query in zip(
            connection.executed_in_transaction, connection.queries, strict=True
        )
        if "insert into" in query.lower()
    ]
    assert all(write_flags)
    audit_index = next(
        i for i, q in enumerate(connection.queries) if "audit_events" in q.lower()
    )
    audit_params = connection.params[audit_index]
    assert audit_params is not None
    assert "HUMAN:OPS_OFFICER" in audit_params
    assert CREATOR in audit_params
    assert DRAFT_ID in audit_params


@pytest.mark.asyncio
async def test_create_draft_without_permitting_decision_raises() -> None:
    connection = Connection(results=[[(CASE_VERSION,)], []])  # version, no decision
    repo = _repo(connection)

    with pytest.raises(DecisionDoesNotPermitNotificationError):
        await repo.create_draft(draft_id=DRAFT_ID, case_id=CASE, created_by=CREATOR)
    # Fail closed before any write.
    assert "insert into" not in _sql(connection)


@pytest.mark.asyncio
async def test_create_draft_with_non_approval_decision_raises() -> None:
    connection = Connection(
        results=[[(CASE_VERSION,)], [_decision_row(decision="DECLINED_BY_HUMAN")]]
    )
    repo = _repo(connection)

    with pytest.raises(DecisionDoesNotPermitNotificationError):
        await repo.create_draft(draft_id=DRAFT_ID, case_id=CASE, created_by=CREATOR)
    assert "insert into" not in _sql(connection)


@pytest.mark.asyncio
async def test_create_draft_is_idempotent_on_existing_version() -> None:
    content = (
        "THÔNG BÁO TÍN DỤNG (DỮ LIỆU MÔ PHỎNG)\nThông báo tín dụng không phải xác "
        "nhận giải ngân."
    )
    connection = Connection(
        results=[
            [(CASE_VERSION,)],
            [_decision_row()],
            [],
            [],  # draft insert -> conflict (no row)
            [_draft_row(content)],  # reselect existing draft
        ]
    )
    repo = _repo(connection)

    recorded = await repo.create_draft(
        draft_id=uuid4(), case_id=CASE, created_by=CREATOR
    )

    assert recorded.created is False
    assert recorded.id == DRAFT_ID
    # No second audit on the idempotent repeat.
    assert "insert into public.audit_events" not in _sql(connection)
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_record_mock_delivery_fails_closed_without_gate() -> None:
    connection = Connection()
    repo = _repo(connection)

    with pytest.raises(GateNotSatisfiedError):
        await repo.record_mock_delivery(
            receipt_id=RECEIPT_ID,
            draft_id=DRAFT_ID,
            content_hash="a" * 64,
            receipt_note_vi=None,
            recorded_by=DELIVERER,
            gate_satisfied=False,
        )
    # Nothing was touched.
    assert connection.queries == []


@pytest.mark.asyncio
async def test_record_mock_delivery_writes_receipt_and_audit_in_one_transaction() -> None:
    content_hash = "a" * 64
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, content_hash)],  # load draft row
            [(RECEIPT_ID, NOW)],  # receipt insert -> inserted
            [],  # audit insert
        ]
    )
    repo = _repo(connection)

    receipt = await repo.record_mock_delivery(
        receipt_id=RECEIPT_ID,
        draft_id=DRAFT_ID,
        content_hash=content_hash,
        receipt_note_vi="Đã giao mock (dữ liệu mô phỏng).",
        recorded_by=DELIVERER,
        gate_satisfied=True,
    )

    assert receipt.delivered_via == MOCK_DELIVERY_CHANNEL
    assert receipt.content_hash == content_hash
    sql = _sql(connection)
    assert "insert into public.communication_receipts" in sql
    assert "on conflict (draft_id) do nothing" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    audit_index = next(
        i for i, q in enumerate(connection.queries) if "audit_events" in q.lower()
    )
    audit_params = connection.params[audit_index]
    assert audit_params is not None
    assert "HUMAN:OPS_OFFICER" in audit_params
    assert DELIVERER in audit_params
    assert RECEIPT_ID in audit_params


@pytest.mark.asyncio
async def test_record_mock_delivery_is_idempotent_on_existing_receipt() -> None:
    content_hash = "a" * 64
    existing = (
        RECEIPT_ID,
        DRAFT_ID,
        MOCK_DELIVERY_CHANNEL,
        content_hash,
        "Đã giao mock (dữ liệu mô phỏng).",
        DELIVERER,
        NOW,
    )
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, content_hash)],  # load draft row
            [],  # receipt insert -> conflict (no row)
            [existing],  # reselect existing receipt
        ]
    )
    repo = _repo(connection)

    receipt = await repo.record_mock_delivery(
        receipt_id=uuid4(),
        draft_id=DRAFT_ID,
        content_hash=content_hash,
        receipt_note_vi=None,
        recorded_by=DELIVERER,
        gate_satisfied=True,
    )

    assert receipt.id == RECEIPT_ID
    # No second audit on the idempotent repeat.
    assert "insert into public.audit_events" not in _sql(connection)
    assert connection.transactions_opened == 1
