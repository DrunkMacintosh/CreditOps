"""Contract tests for ``PostgresReportingRepository.load_operations_metrics``.

Mirrors ``tests/contract/postgres/test_work_items_adapter.py``: a fake
connection captures the exact SQL issued, proving the assembly is strictly
read-only and provenance-free -- without a live Postgres.

The load-bearing guarantees pinned here:
  * EVERY statement is a ``select`` -- no insert/update/delete anywhere.
  * NO statement selects a per-case column (case_id, payload, or any case
    content) -- only grouped status/type/stage labels and counts leave the DB.
  * ONE statement per metric family (six families).
  * queue age is computed IN SQL from ``clock_timestamp() - available_at``.
  * rows are mapped into the aggregate metrics structure unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any

import pytest

from creditops.infrastructure.postgres.reporting import PostgresReportingRepository

# Case-content column tokens that must NEVER appear in an aggregate report query.
_FORBIDDEN_COLUMNS = (
    "case_id",
    "input_payload",
    "purpose_vi",
    "detail_vi",
    "content_vi",
    "storage_object_key",
    "original_filename",
    "idempotency_key",
    "artifact_id",
    "disposition_ref",
)


class ScriptedCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


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


def _repo(connection: ScriptedConnection) -> PostgresReportingRepository:
    return PostgresReportingRepository(lambda: ConnectionContext(connection))


def _scripted() -> ScriptedConnection:
    # One result set per metric family, in the adapter's call order:
    # tasks, queue-age, gates, outbox (single row), documents, alerts.
    return ScriptedConnection(
        [
            [("PENDING", 3), ("SUCCEEDED", 7)],
            [("PENDING", "LE_5M", 2), ("RETRY_WAIT", "GT_60M", 1)],
            [("G1_INTAKE_COMPLETE", "SATISFIED", 4)],
            [(5, 2)],
            [("REGISTERED", 6)],
            [("OPEN", 1)],
        ]
    )


@pytest.mark.asyncio
async def test_every_statement_is_a_read_with_no_case_content_columns() -> None:
    connection = _scripted()

    await _repo(connection).load_operations_metrics()

    # Exactly one SQL per metric family (six families).
    assert len(connection.executions) == 6
    for query, _ in connection.executions:
        lowered = query.lower()
        assert lowered.lstrip().startswith("select")
        assert "insert" not in lowered
        assert "update" not in lowered
        assert "delete" not in lowered
        # No case-content column is ever selected.
        for column in _FORBIDDEN_COLUMNS:
            assert column not in lowered


@pytest.mark.asyncio
async def test_queue_age_is_computed_in_sql_from_the_clock() -> None:
    connection = _scripted()

    await _repo(connection).load_operations_metrics()

    joined = "\n".join(query.lower() for query, _ in connection.executions)
    assert "clock_timestamp() - available_at" in joined
    # And the two waiting statuses are the only ones bucketed.
    assert "status in ('pending', 'retry_wait')" in joined


@pytest.mark.asyncio
async def test_outbox_query_reads_only_the_undispatched_backlog() -> None:
    connection = _scripted()

    await _repo(connection).load_operations_metrics()

    outbox_queries = [
        query
        for query, _ in connection.executions
        if "public.outbox_events" in query.lower()
    ]
    assert len(outbox_queries) == 1
    lowered = outbox_queries[0].lower()
    assert "dispatched_at is null" in lowered
    assert "max(dispatch_attempts)" in lowered
    assert "count(*)" in lowered


@pytest.mark.asyncio
async def test_rows_map_into_the_aggregate_metrics_unchanged() -> None:
    connection = _scripted()

    metrics = await _repo(connection).load_operations_metrics()

    assert [(r.status, r.count) for r in metrics.tasks_by_status] == [
        ("PENDING", 3),
        ("SUCCEEDED", 7),
    ]
    assert [(r.status, r.bucket, r.count) for r in metrics.queue_age_buckets] == [
        ("PENDING", "LE_5M", 2),
        ("RETRY_WAIT", "GT_60M", 1),
    ]
    assert [(r.gate_type, r.status, r.count) for r in metrics.human_gates] == [
        ("G1_INTAKE_COMPLETE", "SATISFIED", 4),
    ]
    assert (metrics.outbox.undispatched_count, metrics.outbox.max_attempts) == (5, 2)
    assert [(r.stage, r.count) for r in metrics.documents_by_stage] == [("REGISTERED", 6)]
    assert [(r.status, r.count) for r in metrics.alerts_by_status] == [("OPEN", 1)]


@pytest.mark.asyncio
async def test_empty_outbox_backlog_defaults_to_zero() -> None:
    connection = ScriptedConnection(
        [[], [], [], [], [], []]  # every family empty, incl. no outbox row
    )

    metrics = await _repo(connection).load_operations_metrics()

    assert metrics.outbox.undispatched_count == 0
    assert metrics.outbox.max_attempts == 0
    assert metrics.tasks_by_status == ()
