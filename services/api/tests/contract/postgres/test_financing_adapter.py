"""Contract tests for the Postgres FinancingRequest persistence adapter.

Mirrors ``tests/contract/postgres/test_intake_adapter.py``: a fake connection
captures the exact SQL and parameters the adapter issues, proving the
next-version append shape, the on-conflict retry, and the case-scoped reads
without a live Postgres.  All identifiers here are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.financing_requests import FinancingRequestDraft
from creditops.infrastructure.postgres.financing import PostgresFinancingRepository

CASE = UUID("10000000-0000-0000-0000-000000000007")
VERSION_ID = UUID("20000000-0000-0000-0000-000000000007")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_VERSION = 1
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _draft() -> FinancingRequestDraft:
    return FinancingRequestDraft(
        requested_amount="5000000000",
        purpose_vi="Bổ sung vốn lưu động",
        currency="VND",
        term_months=12,
    )


def _full_row() -> tuple[object, ...]:
    return (
        VERSION_ID,
        CASE,
        CASE_VERSION,
        3,
        "5000000000",
        "Bổ sung vốn lưu động",
        "VND",
        None,
        12,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        OFFICER,
        NOW,
    )


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


def _repo(connection: Connection) -> PostgresFinancingRepository:
    return PostgresFinancingRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


@pytest.mark.asyncio
async def test_append_version_computes_next_version_and_inserts_on_conflict() -> None:
    # max+1 select returns 3; insert returns its created_at.
    connection = Connection(results=[[(3,)], [(NOW,)]])
    repo = _repo(connection)

    version = await repo.append_version(
        case_id=CASE, case_version=CASE_VERSION, fields=_draft(), actor_id=OFFICER
    )

    assert version.request_version == 3
    assert version.case_id == CASE
    assert version.created_by == OFFICER
    sql = _sql(connection)
    assert "coalesce(max(request_version), 0) + 1" in sql
    assert "insert into public.financing_requests" in sql
    assert "on conflict (case_id, request_version) do nothing" in sql
    # The whole append runs inside one transaction; identifiers are bound.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    insert_index = next(
        i for i, q in enumerate(connection.queries) if "insert into" in q.lower()
    )
    insert_params = connection.params[insert_index]
    assert insert_params is not None
    assert CASE in insert_params
    assert CASE_VERSION in insert_params
    assert 3 in insert_params
    assert OFFICER in insert_params


@pytest.mark.asyncio
async def test_append_version_retries_on_unique_conflict() -> None:
    # Attempt 1: max+1 -> 2, insert conflicts (no returning row).
    # Attempt 2: max+1 -> 3, insert succeeds.
    connection = Connection(results=[[(2,)], [], [(3,)], [(NOW,)]])
    repo = _repo(connection)

    version = await repo.append_version(
        case_id=CASE, case_version=CASE_VERSION, fields=_draft(), actor_id=OFFICER
    )

    assert version.request_version == 3
    # One transaction per attempt: the conflict re-ran under a fresh transaction.
    assert connection.transactions_opened == 2


@pytest.mark.asyncio
async def test_list_versions_is_scoped_by_case() -> None:
    connection = Connection(results=[[_full_row()]])
    repo = _repo(connection)

    versions = await repo.list_versions(CASE)

    assert len(versions) == 1
    assert versions[0].request_version == 3
    assert versions[0].requested_amount == "5000000000"
    sql = _sql(connection)
    assert "from public.financing_requests" in sql
    assert "where case_id = %s" in sql
    assert connection.params[0] == (CASE,)


@pytest.mark.asyncio
async def test_latest_version_orders_by_version_desc_scoped_by_case() -> None:
    connection = Connection(results=[[_full_row()]])
    repo = _repo(connection)

    latest = await repo.latest_version(CASE)

    assert latest is not None
    assert latest.request_version == 3
    sql = _sql(connection)
    assert "from public.financing_requests" in sql
    assert "order by request_version desc" in sql
    assert "where case_id = %s" in sql
    assert connection.params[0] == (CASE,)


@pytest.mark.asyncio
async def test_latest_version_returns_none_when_empty() -> None:
    connection = Connection()
    repo = _repo(connection)

    assert await repo.latest_version(CASE) is None


@pytest.mark.asyncio
async def test_append_audit_writes_a_human_intake_officer_event() -> None:
    connection = Connection()
    repo = _repo(connection)

    await repo.append_audit(
        OrchestrationAuditEvent(
            case_id=CASE,
            case_version=CASE_VERSION,
            event_type="FINANCING_NEED_CONFIRMED",
            execution_id=uuid4(),
            artifact_type="FINANCING_REQUEST_VERSION",
            artifact_id=VERSION_ID,
        )
    )

    sql = _sql(connection)
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    params = connection.params[0]
    assert params is not None
    assert "HUMAN:INTAKE_OFFICER" in params
