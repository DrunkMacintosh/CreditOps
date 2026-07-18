"""Contract tests for the Postgres prospect persistence adapter.

Mirrors ``tests/contract/postgres/test_intake_adapter.py`` and
``test_audit_events_query.py``: a fake connection captures the exact SQL and
parameters the adapter issues, proving append-only inserts, keyset pagination,
and created_by scoping on every read -- without a live Postgres.  All
identifiers here are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.prospects import ProspectNotFound
from creditops.domain.prospects import ContactDecision
from creditops.infrastructure.postgres.prospects import PostgresProspectRepository

OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROSPECT = UUID("10000000-0000-0000-0000-0000000000a1")
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
        self.params: list[tuple[object, ...] | list[object] | None] = []
        self.transaction_depth = 0
        self.transactions_opened = 0
        self.executed_in_transaction: list[bool] = []

    def transaction(self) -> Transaction:
        return Transaction(self)

    async def execute(
        self, query: str, params: tuple[object, ...] | list[object] | None = None
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


def _repo(connection: Connection) -> PostgresProspectRepository:
    return PostgresProspectRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _prospect_row(prospect_id: UUID = PROSPECT) -> tuple[object, ...]:
    return (
        prospect_id,
        "Cong ty Demo",
        "Nong nghiep",
        7,
        "Duoi 100 ty",
        "Dang hoat dong",
        "ghi chu",
        OFFICER,
        NOW,
    )


@pytest.mark.asyncio
async def test_create_prospect_inserts_append_only_in_one_transaction() -> None:
    connection = Connection(results=[[(PROSPECT, NOW)]])
    repo = _repo(connection)

    prospect = await repo.create_prospect(
        name_vi="Cong ty Demo",
        industry_vi="Nong nghiep",
        years_operating=7,
        revenue_band_vi=None,
        legal_status_vi=None,
        notes_vi=None,
        created_by=OFFICER,
    )

    assert prospect.id == PROSPECT
    assert prospect.created_by == OFFICER
    sql = _sql(connection)
    assert "insert into public.prospects" in sql
    # Append-only: never an update or delete.
    assert "update public.prospects" not in sql
    assert "delete" not in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    params = connection.params[0]
    assert params is not None
    assert OFFICER in params


@pytest.mark.asyncio
async def test_list_prospects_is_created_by_scoped_keyset_without_cursor() -> None:
    connection = Connection(results=[[_prospect_row()]])
    repo = _repo(connection)

    page, next_cursor = await repo.list_prospects(
        created_by=OFFICER, cursor=None, limit=50
    )

    assert len(page) == 1
    assert next_cursor is None
    query = connection.queries[0].lower()
    assert "from public.prospects" in query
    assert "where created_by = %s" in query
    assert "order by created_at desc, id desc" in query
    params = connection.params[0]
    assert params is not None
    assert params[0] == OFFICER
    assert params[-1] == 51  # limit + 1
    # No cursor: the subselect fragment must not appear.
    assert "select created_at, id" not in query


@pytest.mark.asyncio
async def test_list_prospects_cursor_subselect_is_created_by_scoped() -> None:
    connection = Connection(results=[[_prospect_row()]])
    cursor_id = uuid4()
    repo = _repo(connection)

    await repo.list_prospects(created_by=OFFICER, cursor=cursor_id, limit=10)

    assert len(connection.queries) == 1  # single round trip
    query = connection.queries[0].lower()
    assert "select created_at, id" in query
    assert "< (" in query
    params = connection.params[0]
    assert params is not None
    assert cursor_id in params
    # created_by is bound for the outer scope AND the subselect scope, so a
    # foreign officer's cursor id can never be resolved.
    assert list(params).count(OFFICER) == 2
    assert params[-1] == 11  # limit + 1


@pytest.mark.asyncio
async def test_list_prospects_detects_next_page_via_limit_plus_one() -> None:
    rows = [_prospect_row(uuid4()) for _ in range(3)]
    connection = Connection(results=[rows])
    repo = _repo(connection)

    page, next_cursor = await repo.list_prospects(
        created_by=OFFICER, cursor=None, limit=2
    )

    assert len(page) == 2
    assert next_cursor == page[1].id


@pytest.mark.asyncio
async def test_load_prospect_scopes_every_read_by_created_by() -> None:
    snapshot_row = (
        uuid4(),
        PROSPECT,
        1,
        "cfg-v1",
        "Nong nghiep",
        7,
        None,
        None,
        None,
        None,
        {},
        OFFICER,
        NOW,
    )
    decision_row = (uuid4(), PROSPECT, "CONTACT", "ly do", OFFICER, NOW)
    connection = Connection(
        results=[[_prospect_row()], [snapshot_row], [decision_row]]
    )
    repo = _repo(connection)

    detail = await repo.load_prospect(prospect_id=PROSPECT, created_by=OFFICER)

    assert detail is not None
    assert detail.prospect.id == PROSPECT
    assert detail.latest_snapshot is not None
    assert detail.latest_snapshot.version == 1
    assert len(detail.decisions) == 1
    # Every read is scoped by created_by (the prospect gate directly, the
    # children via a join on the owning prospect).
    assert len(connection.queries) == 3
    for query, params in zip(connection.queries, connection.params, strict=True):
        assert "created_by = %s" in query.lower()
        assert params is not None
        assert OFFICER in params


@pytest.mark.asyncio
async def test_load_prospect_returns_none_for_unowned_and_reads_nothing_else() -> None:
    connection = Connection(results=[[]])  # prospect gate returns nothing
    repo = _repo(connection)

    detail = await repo.load_prospect(prospect_id=PROSPECT, created_by=OFFICER)

    assert detail is None
    # The ownership gate short-circuits: no snapshot / decision read happens.
    assert len(connection.queries) == 1
    assert "where id = %s and created_by = %s" in connection.queries[0].lower()


@pytest.mark.asyncio
async def test_append_snapshot_checks_ownership_then_inserts_append_only() -> None:
    # ownership gate -> owned; next-version -> 2; insert -> returning row.
    connection = Connection(results=[[(1,)], [(2,)], [(uuid4(), NOW)]])
    repo = _repo(connection)

    snapshot = await repo.append_screening_snapshot(
        prospect_id=PROSPECT,
        created_by=OFFICER,
        screening_config_version="cfg-v1",
        industry_vi=None,
        years_operating=None,
        revenue_band_vi=None,
        legal_status_vi=None,
        credit_history_vi=None,
        risk_appetite_note_vi=None,
        details={},
    )

    assert snapshot.version == 2
    sql = _sql(connection)
    # Ownership gate is created_by-scoped and runs first.
    assert "select 1 from public.prospects" in sql
    assert "where id = %s and created_by = %s" in sql
    assert "insert into public.prospect_screening_snapshots" in sql
    assert "update public.prospect_screening_snapshots" not in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


@pytest.mark.asyncio
async def test_append_snapshot_fails_closed_without_ownership() -> None:
    connection = Connection(results=[[]])  # ownership gate finds nothing
    repo = _repo(connection)

    with pytest.raises(ProspectNotFound):
        await repo.append_screening_snapshot(
            prospect_id=PROSPECT,
            created_by=OFFICER,
            screening_config_version="cfg-v1",
            industry_vi=None,
            years_operating=None,
            revenue_band_vi=None,
            legal_status_vi=None,
            credit_history_vi=None,
            risk_appetite_note_vi=None,
            details={},
        )

    assert "insert into public.prospect_screening_snapshots" not in _sql(connection)


@pytest.mark.asyncio
async def test_record_decision_checks_ownership_then_inserts_append_only() -> None:
    connection = Connection(results=[[(1,)], [(uuid4(), NOW)]])
    repo = _repo(connection)

    decision = await repo.record_contact_decision(
        prospect_id=PROSPECT,
        created_by=OFFICER,
        decision=ContactDecision.CONTACT,
        rationale_vi="ly do lien he",
        decided_by=OFFICER,
    )

    assert decision.decision is ContactDecision.CONTACT
    sql = _sql(connection)
    assert "select 1 from public.prospects" in sql
    assert "insert into public.prospect_contact_decisions" in sql
    assert "update public.prospect_contact_decisions" not in sql
    assert "delete" not in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


@pytest.mark.asyncio
async def test_record_decision_fails_closed_without_ownership() -> None:
    connection = Connection(results=[[]])
    repo = _repo(connection)

    with pytest.raises(ProspectNotFound):
        await repo.record_contact_decision(
            prospect_id=PROSPECT,
            created_by=OFFICER,
            decision=ContactDecision.DEFER,
            rationale_vi="ly do",
            decided_by=OFFICER,
        )

    assert "insert into public.prospect_contact_decisions" not in _sql(connection)
