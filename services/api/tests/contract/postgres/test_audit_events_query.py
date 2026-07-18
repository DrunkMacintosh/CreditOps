"""Contract tests for ``PostgresOrchestrationRepository.list_audit_events``.

Mirrors ``tests/contract/supabase/test_queue_redelivery.py``: a fake
connection captures the exact SQL and parameters issued, proving the query
shape (newest-first keyset pagination, case-scoped subselect cursor
resolution, limit+1 next-page detection) without a live Postgres instance.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.orchestration import AuditEventRow
from creditops.infrastructure.postgres.orchestration import (
    PostgresOrchestrationRepository,
)

CASE = UUID("10000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)


class Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class Connection:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.queries: list[str] = []
        self.params: list[Any] = []

    async def execute(self, query: str, params: Any = None) -> Cursor:
        self.queries.append(query)
        self.params.append(params)
        return Cursor(self.rows)


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


def _repo(connection: Connection) -> PostgresOrchestrationRepository:
    return PostgresOrchestrationRepository(lambda: ConnectionContext(connection))


def _row(*, seq: int, actor_id: UUID | None = None) -> tuple[Any, ...]:
    return (
        UUID(f"70000000-0000-0000-0000-{seq:012d}"),
        CASE,
        1,
        "CASE_CREATED",
        "HUMAN" if actor_id is not None else "AGENT:CASE_ORCHESTRATOR",
        actor_id,
        "CREDIT_CASE",
        CASE,
        {"note": f"event-{seq}"},
        NOW,
    )


@pytest.mark.asyncio
async def test_list_audit_events_orders_desc_and_binds_case_and_limit() -> None:
    connection = Connection([_row(seq=0)])
    repo = _repo(connection)

    events, next_cursor = await repo.list_audit_events(CASE, cursor=None, limit=50)

    assert len(events) == 1
    assert next_cursor is None
    query = connection.queries[0].lower()
    assert "from public.audit_events" in query
    assert "where case_id = %s" in query
    assert "order by created_at desc, id desc" in query
    params = connection.params[0]
    assert params[0] == CASE
    assert params[-1] == 51  # limit + 1
    # No cursor supplied: the subselect fragment must not appear at all.
    assert "select created_at, id" not in query


@pytest.mark.asyncio
async def test_list_audit_events_resolves_cursor_via_subselect_in_the_same_query() -> None:
    connection = Connection([_row(seq=1)])
    cursor_id = uuid4()
    repo = _repo(connection)

    await repo.list_audit_events(CASE, cursor=cursor_id, limit=10)

    # Exactly one round trip: the cursor's (created_at, id) position is
    # resolved by a subselect embedded in this query -- never a preceding
    # lookup query, unlike the two-query pattern in cases.py::list_assigned.
    assert len(connection.queries) == 1
    query = connection.queries[0].lower()
    assert "select created_at, id" in query
    assert "from public.audit_events" in query
    assert "< (" in query  # strict keyset comparison against the subselect
    params = connection.params[0]
    assert cursor_id in params
    # case_id is bound twice: once for the outer scope, once for the
    # subselect's case-scoping (so a foreign case's id can't be resolved).
    assert params.count(CASE) == 2
    assert params[-1] == 11  # limit + 1


@pytest.mark.asyncio
async def test_list_audit_events_detects_next_page_via_limit_plus_one() -> None:
    rows = [_row(seq=index) for index in range(3)]
    connection = Connection(rows)
    repo = _repo(connection)

    events, next_cursor = await repo.list_audit_events(CASE, cursor=None, limit=2)

    assert len(events) == 2
    assert events[0].id == rows[0][0]
    assert events[1].id == rows[1][0]
    assert next_cursor == rows[1][0]


@pytest.mark.asyncio
async def test_list_audit_events_exact_page_has_no_next_cursor() -> None:
    rows = [_row(seq=index) for index in range(2)]
    connection = Connection(rows)
    repo = _repo(connection)

    events, next_cursor = await repo.list_audit_events(CASE, cursor=None, limit=2)

    assert len(events) == 2
    assert next_cursor is None


@pytest.mark.asyncio
async def test_list_audit_events_maps_row_columns_including_null_actor() -> None:
    connection = Connection([_row(seq=0, actor_id=None)])
    repo = _repo(connection)

    events, _ = await repo.list_audit_events(CASE, cursor=None, limit=50)

    [event] = events
    assert isinstance(event, AuditEventRow)
    assert event.id == connection.rows[0][0]
    assert event.case_id == CASE
    assert event.case_version == 1
    assert event.event_type == "CASE_CREATED"
    assert event.actor_type == "AGENT:CASE_ORCHESTRATOR"
    assert event.actor_id is None
    assert event.artifact_type == "CREDIT_CASE"
    assert event.artifact_id == CASE
    assert event.event_data == {"note": "event-0"}
    assert event.created_at == NOW
