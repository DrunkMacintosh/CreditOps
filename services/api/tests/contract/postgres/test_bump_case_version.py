"""Contract test for ``PostgresOrchestrationRepository.bump_case_version``.

Mirrors ``tests/contract/postgres/test_intake_adapter.py``: a fake connection
captures the exact SQL and parameters the adapter issues, proving the
single-transaction shape of the optimistic version bump + CASE_VERSION_BUMPED
audit row + intake-handoff re-issue (master design section 9) without a live
Postgres.  All identifiers here are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from creditops.application.ports.orchestration import StaleCaseVersionError
from creditops.infrastructure.postgres.orchestration import (
    PostgresOrchestrationRepository,
)

CASE = UUID("10000000-0000-0000-0000-000000000009")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
EXPECTED_VERSION = 1
NEW_VERSION = 2
DISPOSITION_REF = "risk-review-disposition:70000000-0000-0000-0000-0000000000aa"
REASON = "Can bo sung can cu cho gia dinh dong tien."


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


def _repo(connection: Connection) -> PostgresOrchestrationRepository:
    return PostgresOrchestrationRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


@pytest.mark.asyncio
async def test_bump_is_one_transaction_with_optimistic_guard_audit_and_reissue() -> None:
    # UPDATE ... RETURNING case_version -> the new version; the audit + handoff
    # re-issue inserts return nothing.
    connection = Connection(results=[[(NEW_VERSION,)]])
    repo = _repo(connection)

    new_version = await repo.bump_case_version(
        CASE,
        expected_version=EXPECTED_VERSION,
        reason=REASON,
        disposition_ref=DISPOSITION_REF,
        actor_id=OFFICER,
    )

    assert new_version == NEW_VERSION
    # All three writes run inside exactly one transaction.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    assert len(connection.queries) == 3

    # 1. Optimistic bump: increment guarded by the expected version, RETURNING.
    update_sql = connection.queries[0].lower()
    assert "update public.credit_cases" in update_sql
    assert "set case_version = case_version + 1" in update_sql
    assert "where id = %s and case_version = %s" in update_sql
    assert "returning case_version" in update_sql
    assert connection.params[0] == (CASE, EXPECTED_VERSION)

    # 2. CASE_VERSION_BUMPED audit row at the NEW version, human provenance.
    audit_sql = connection.queries[1].lower()
    assert "insert into public.audit_events" in audit_sql
    assert "case_version_bumped" in audit_sql
    audit_params = connection.params[1]
    assert audit_params is not None
    assert CASE in audit_params
    assert NEW_VERSION in audit_params
    assert OFFICER in audit_params
    assert "HUMAN:RISK_REVIEWER" in audit_params
    event_data = next(p for p in audit_params if isinstance(p, Jsonb)).obj
    assert event_data["reason"] == REASON
    assert event_data["dispositionRef"] == DISPOSITION_REF
    assert event_data["previousVersion"] == EXPECTED_VERSION
    assert event_data["newVersion"] == NEW_VERSION

    # 3. Re-issue the intake handoff at the new version by cloning the latest
    # READY handoff from the old version (INSERT ... SELECT), preserving the
    # source task and merging a provenance note into the immutable handoff_data.
    handoff_sql = connection.queries[2].lower()
    assert "insert into public.handoffs" in handoff_sql
    assert "select" in handoff_sql
    assert "source_task_id" in handoff_sql
    assert "handoff_data || %s::jsonb" in handoff_sql
    assert "state = 'ready_for_specialist_review'" in handoff_sql
    assert "stale_at is null" in handoff_sql
    handoff_params = connection.params[2]
    assert handoff_params is not None
    # new version to write, old version to clone from, both bound as params.
    assert NEW_VERSION in handoff_params
    assert EXPECTED_VERSION in handoff_params
    assert CASE in handoff_params
    provenance = next(p for p in handoff_params if isinstance(p, Jsonb)).obj
    assert provenance["revisionProvenance"]["reissuedFromVersion"] == EXPECTED_VERSION
    assert provenance["revisionProvenance"]["dispositionRef"] == DISPOSITION_REF


@pytest.mark.asyncio
async def test_stale_expected_version_raises_and_writes_nothing_else() -> None:
    # UPDATE ... RETURNING matches no row (case already moved on): the guard
    # fetch is empty, so the bump fails closed and never writes the audit row
    # or re-issues a handoff.
    connection = Connection(results=[[]])
    repo = _repo(connection)

    with pytest.raises(StaleCaseVersionError):
        await repo.bump_case_version(
            CASE,
            expected_version=EXPECTED_VERSION,
            reason=REASON,
            disposition_ref=DISPOSITION_REF,
            actor_id=OFFICER,
        )

    # Only the optimistic UPDATE was attempted; no audit or handoff write.
    assert len(connection.queries) == 1
    sql = _sql(connection)
    assert "update public.credit_cases" in sql
    assert "insert into public.audit_events" not in sql
    assert "insert into public.handoffs" not in sql
    # The failed attempt still ran inside the transaction.
    assert connection.transactions_opened == 1
