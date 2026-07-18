"""Contract tests for ``PostgresWorkItemRepository.list_for_actor``.

Mirrors ``tests/contract/postgres/test_audit_events_query.py``: a fake
connection captures the exact SQL and parameters issued, proving the assembly
is strictly read-only and correctly scoped -- without a live Postgres.

The load-bearing guarantees pinned here:
  * EVERY statement is a ``select`` -- no insert/update/delete anywhere.
  * every statement is scoped by ``officer_id`` = the actor, ``revoked_at is
    null``, and a ``case_role`` predicate (the assignment side of the JWT/role
    AND).
  * role gating decides which selects run; empty roles run none.
  * the participant-wide task rules bind ``case_role = any(<authorized roles>)``
    and the task status literal.
  * assembly orders BLOCKING first, then created_at desc, and clamps to limit.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest

from creditops.infrastructure.postgres.work_items import PostgresWorkItemRepository

ACTOR = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)

INTAKE = "INTAKE_OFFICER"
RISK = "RISK_REVIEWER"
OPS = "OPS_OFFICER"


class ScriptedCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class ScriptedConnection:
    """Returns the next scripted result set per ``execute`` and records SQL."""

    def __init__(self, results: list[list[tuple[Any, ...]]] | None = None) -> None:
        self._results = list(results or [])
        self.executions: list[tuple[str, Sequence[Any] | None]] = []

    async def execute(
        self, query: str, params: Sequence[Any] | None = None
    ) -> ScriptedCursor:
        self.executions.append((query, params))
        rows = self._results.pop(0) if self._results else []
        return ScriptedCursor(rows)


class ConnectionContext(AbstractAsyncContextManager[ScriptedConnection]):
    def __init__(self, connection: ScriptedConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> ScriptedConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _repo(connection: ScriptedConnection) -> PostgresWorkItemRepository:
    return PostgresWorkItemRepository(lambda: ConnectionContext(connection))


def _queries(connection: ScriptedConnection) -> list[str]:
    return [query.lower() for query, _ in connection.executions]


@pytest.mark.asyncio
async def test_every_statement_is_a_read_scoped_by_officer_role_and_active() -> None:
    connection = ScriptedConnection()
    repo = _repo(connection)

    await repo.list_for_actor(ACTOR, frozenset({INTAKE, RISK, OPS}), limit=50)

    # All four role rules plus the two participant-wide task rules ran.
    assert len(connection.executions) == 6
    for query, params in connection.executions:
        lowered = query.lower()
        # Strictly read-only: only SELECTs, never a write.
        assert lowered.lstrip().startswith("select")
        assert "insert" not in lowered
        assert "update" not in lowered
        assert "delete" not in lowered
        # Assignment-side scoping present in every statement.
        assert "public.case_assignments" in lowered
        assert "officer_id = %s" in lowered
        assert "revoked_at is null" in lowered
        assert "case_role" in lowered
        # The actor is always the first bound parameter.
        assert params is not None
        assert params[0] == ACTOR


@pytest.mark.asyncio
async def test_role_gating_controls_which_selects_run() -> None:
    # INTAKE only: 2 intake selects + 2 task selects.
    intake_conn = ScriptedConnection()
    await _repo(intake_conn).list_for_actor(ACTOR, frozenset({INTAKE}), limit=50)
    assert len(intake_conn.executions) == 4

    # RISK only: 1 risk select + 2 task selects.
    risk_conn = ScriptedConnection()
    await _repo(risk_conn).list_for_actor(ACTOR, frozenset({RISK}), limit=50)
    assert len(risk_conn.executions) == 3

    # OPS only: 1 ops select + 2 task selects.
    ops_conn = ScriptedConnection()
    await _repo(ops_conn).list_for_actor(ACTOR, frozenset({OPS}), limit=50)
    assert len(ops_conn.executions) == 3


@pytest.mark.asyncio
async def test_empty_roles_run_no_selects_and_return_nothing() -> None:
    connection = ScriptedConnection()

    items = await _repo(connection).list_for_actor(ACTOR, frozenset(), limit=50)

    assert items == ()
    assert connection.executions == []


@pytest.mark.asyncio
async def test_role_specific_selects_reference_their_gate_and_bind_limit_last() -> None:
    connection = ScriptedConnection()

    await _repo(connection).list_for_actor(ACTOR, frozenset({INTAKE, RISK, OPS}), limit=17)

    joined = "\n".join(_queries(connection))
    assert "g2_gap_request_approval" in joined
    assert "g3_risk_disposition" in joined
    assert "g4_ops_authorization" in joined
    assert "risk_review_assessments" in joined
    assert "credit_ops_packages" in joined
    # limit is bound as the final parameter of every statement.
    for _, params in connection.executions:
        assert params is not None
        assert params[-1] == 17


@pytest.mark.asyncio
async def test_task_rules_bind_case_role_any_of_authorized_roles_and_status() -> None:
    connection = ScriptedConnection()

    await _repo(connection).list_for_actor(ACTOR, frozenset({INTAKE, RISK}), limit=50)

    task_execs = [
        (query, params)
        for query, params in connection.executions
        if "processing_tasks" in query.lower()
    ]
    assert len(task_execs) == 2
    statuses = set()
    for query, params in task_execs:
        assert "any(%s)" in query.lower()
        assert params is not None
        # (actor_id, authorized_roles_list, status, limit)
        assert params[0] == ACTOR
        assert params[1] == [INTAKE, RISK]  # sorted authorized roles
        statuses.add(params[2])
    assert statuses == {"FAILED_MANUAL_REVIEW", "RETRY_WAIT"}


@pytest.mark.asyncio
async def test_assembles_orders_blocking_first_then_created_at_desc() -> None:
    case_intake = UUID("11111111-1111-4111-8111-111111111111")
    case_gap = UUID("22222222-2222-4222-8222-222222222222")
    case_manual = UUID("33333333-3333-4333-8333-333333333333")
    case_retry = UUID("44444444-4444-4444-8444-444444444444")
    connection = ScriptedConnection(
        [
            [(case_intake, 1, NOW - timedelta(minutes=1))],  # INTAKE_INCOMPLETE
            [(case_gap, 1, NOW - timedelta(minutes=3))],  # GAP_BATCH_PENDING
            [],  # RISK_DISPOSITION_PENDING
            [],  # OPS_AUTHORIZATION_PENDING
            [(case_manual, 1, NOW - timedelta(minutes=10))],  # MANUAL_REVIEW (BLOCKING)
            [(case_retry, 1, NOW - timedelta(minutes=2))],  # RETRY_WAIT (INFO)
        ]
    )

    items = await _repo(connection).list_for_actor(
        ACTOR, frozenset({INTAKE, RISK, OPS}), limit=50
    )

    assert [item.kind for item in items] == [
        "MANUAL_REVIEW",  # BLOCKING first, despite being the oldest
        "INTAKE_INCOMPLETE",  # then newest-first among the rest
        "RETRY_WAIT",
        "GAP_BATCH_PENDING",
    ]
    assert [item.severity for item in items] == ["BLOCKING", "ATTENTION", "INFO", "ATTENTION"]
    # Concrete route formatting is per-item (case id substituted).
    manual = items[0]
    assert manual.primary_route == f"/ho-so/{case_manual}/quy-trinh"
    assert manual.title_vi and manual.reason_vi  # copy drawn from the mapping


@pytest.mark.asyncio
async def test_overall_limit_clamps_the_assembled_list() -> None:
    case_manual = UUID("33333333-3333-4333-8333-333333333333")
    case_a = UUID("11111111-1111-4111-8111-111111111111")
    case_b = UUID("22222222-2222-4222-8222-222222222222")
    connection = ScriptedConnection(
        [
            [(case_a, 1, NOW - timedelta(minutes=1))],  # INTAKE_INCOMPLETE
            [(case_b, 1, NOW - timedelta(minutes=2))],  # GAP_BATCH_PENDING
            [],  # RISK
            [],  # OPS
            [(case_manual, 1, NOW - timedelta(minutes=5))],  # MANUAL_REVIEW (BLOCKING)
            [],  # RETRY_WAIT
        ]
    )

    items = await _repo(connection).list_for_actor(
        ACTOR, frozenset({INTAKE, RISK, OPS}), limit=2
    )

    assert len(items) == 2
    assert [item.kind for item in items] == ["MANUAL_REVIEW", "INTAKE_INCOMPLETE"]
