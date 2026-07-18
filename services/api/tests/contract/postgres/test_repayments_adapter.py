"""Contract tests for the Postgres RepaymentLedger adapter.

Mirrors ``tests/contract/postgres/test_conditions_adapter.py``: a fake connection
captures the exact SQL and parameters the adapter issues, proving each write is
ONE transaction (append-only row + HUMAN audit), that ``record_event`` is
idempotent (a duplicate delivery re-selects the existing row and writes NO second
audit), and that reads issue the ordered selects -- all without a live Postgres.
All identifiers are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from uuid import UUID

import pytest

from creditops.application.ports.repayments import RecordedRepaymentEvent
from creditops.domain.repayments import EventKind, Facility, RepaymentEvent
from creditops.infrastructure.postgres.repayments import (
    PostgresRepaymentLedgerRepository,
)

CASE = UUID("10000000-0000-0000-0000-0000000000f3")
DECISION = UUID("d0000000-0000-0000-0000-0000000000f3")
FACILITY = UUID("fac00000-0000-0000-0000-0000000000f3")
EVENT = UUID("ea000000-0000-0000-0000-00000000000a")
PAYMENT = UUID("eb000000-0000-0000-0000-00000000000b")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_VERSION = 3
NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


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


def _repo(connection: Connection) -> PostgresRepaymentLedgerRepository:
    return PostgresRepaymentLedgerRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _facility() -> Facility:
    return Facility(
        id=FACILITY,
        case_id=CASE,
        case_version=CASE_VERSION,
        decision_id=DECISION,
        principal=Decimal("120000.00"),
        annual_rate_percent=Decimal("12"),
        term_months=3,
        repayment_style="EQUAL_PRINCIPAL",
        first_payment_date=date(2026, 8, 1),
        periodic_fee=Decimal("100.00"),
    )


def _payment_event() -> RepaymentEvent:
    return RepaymentEvent(
        id=EVENT,
        facility_id=FACILITY,
        kind=EventKind.PAYMENT,
        amount=Decimal("41300.00"),
        external_reference="BANKREF-0001",
        effective_date=date(2026, 8, 1),
    )


def _event_row(external_reference: str = "BANKREF-0001") -> tuple[object, ...]:
    return (
        EVENT,
        FACILITY,
        "PAYMENT",
        "41300.00",
        external_reference,
        None,
        date(2026, 8, 1),
        NOW,
    )


@pytest.mark.asyncio
async def test_create_facility_is_one_transaction_with_audit() -> None:
    # insert facilities -> created_at; audit insert -> none.
    connection = Connection(results=[[(NOW,)], []])
    repo = _repo(connection)

    recorded = await repo.create_facility(
        facility=_facility(), actor_id=OFFICER, actor_role="OPS_OFFICER"
    )

    assert recorded.id == FACILITY
    assert recorded.principal == Decimal("120000.00")
    assert recorded.created_at == NOW

    sql = _sql(connection)
    assert "insert into public.facilities" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    audit_index = next(
        i for i, q in enumerate(connection.queries) if "audit_events" in q.lower()
    )
    assert "HUMAN:OPS_OFFICER" in (connection.params[audit_index] or ())
    # Money is persisted as exact Decimal-as-text, never a float.
    facility_index = next(
        i for i, q in enumerate(connection.queries) if "public.facilities" in q.lower()
    )
    assert "120000.00" in (connection.params[facility_index] or ())


@pytest.mark.asyncio
async def test_record_event_new_writes_row_and_audit() -> None:
    # insert -> (id, recorded_at); facility-case select -> (case, version); audit.
    connection = Connection(
        results=[[(EVENT, NOW)], [(CASE, CASE_VERSION)], []]
    )
    repo = _repo(connection)

    recorded, created = await repo.record_event(
        event=_payment_event(), actor_id=OFFICER, actor_role="OPS_OFFICER"
    )

    assert created is True
    assert recorded.id == EVENT
    assert recorded.amount == Decimal("41300.00")

    sql = _sql(connection)
    assert "insert into public.repayment_events" in sql
    assert "on conflict (facility_id, external_reference) do nothing" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_record_event_duplicate_is_idempotent_no_audit() -> None:
    # insert on conflict -> no row; re-select -> existing row.  No audit written.
    connection = Connection(results=[[], [_event_row()]])
    repo = _repo(connection)

    recorded, created = await repo.record_event(
        event=_payment_event(), actor_id=OFFICER, actor_role="OPS_OFFICER"
    )

    assert created is False
    assert recorded.id == EVENT
    assert recorded.external_reference == "BANKREF-0001"

    sql = _sql(connection)
    assert "insert into public.repayment_events" in sql
    # The duplicate path writes NO second economic effect and NO audit event.
    assert "insert into public.audit_events" not in sql


@pytest.mark.asyncio
async def test_list_events_orders_the_history() -> None:
    connection = Connection(results=[[_event_row("A"), _event_row("B")]])
    repo = _repo(connection)

    events: tuple[RecordedRepaymentEvent, ...] = await repo.list_events(FACILITY)

    assert [e.external_reference for e in events] == ["A", "B"]
    sql = _sql(connection)
    assert "from public.repayment_events" in sql
    assert "order by effective_date asc, recorded_at asc, id asc" in sql


@pytest.mark.asyncio
async def test_record_collection_note_is_one_transaction_with_audit() -> None:
    connection = Connection(results=[[(NOW,)], []])
    repo = _repo(connection)

    note = await repo.record_collection_note(
        facility_id=FACILITY,
        case_id=CASE,
        case_version=CASE_VERSION,
        note_kind="PROPOSED_ACTION",
        note_text_vi="Đề xuất siết dòng tiền.",
        proposed_action_vi="TIGHTEN_CASHFLOW_CONTROL",
        actor_id=OFFICER,
        actor_role="OPS_OFFICER",
    )

    assert note.proposed_action_vi == "TIGHTEN_CASHFLOW_CONTROL"
    assert note.created_at == NOW

    sql = _sql(connection)
    assert "insert into public.collection_notes" in sql
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
