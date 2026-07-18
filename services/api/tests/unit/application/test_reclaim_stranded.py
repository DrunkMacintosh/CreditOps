"""Stranded-task recovery sweep (``TaskRepository.reclaim_stranded``).

A worker that crashes mid-run leaves its task ``RUNNING`` with a lease that
eventually expires.  ``claim`` only matches ``PENDING``/``RETRY_WAIT``, so
without a sweep the row is never reclaimable and its queue message redelivers
forever.  These tests pin the intended reset semantics (a Python reference
model of ``processing_tasks``) and, separately, the real SQL emitted by
``PostgresTaskRepository`` (contract-style fake-connection pattern copied from
``tests/contract/supabase/test_queue_redelivery.py``).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from creditops.application.use_cases.run_worker_once import (
    RunWorkerOnce,
    WorkerOutcome,
)
from creditops.domain.enums import TaskStatus
from creditops.infrastructure.postgres.tasks import PostgresTaskRepository

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
CASE = UUID("10000000-0000-0000-0000-000000000001")
TASK_A = UUID("30000000-0000-0000-0000-00000000000a")
TASK_B = UUID("30000000-0000-0000-0000-00000000000b")

# The recovery sweep mirrors the worker's default retry base delay so a
# reclaimed task waits exactly as long as a live worker's retry would have.
_BASE_DELAY_SECONDS = 30


@dataclass
class _Row:
    id: UUID
    status: TaskStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    lease_token: UUID | None = None
    lease_until: datetime | None = None
    failure_reason: str | None = None
    completed_at: datetime | None = None


@dataclass
class _ProcessingTasks:
    """In-memory reference model of the relevant ``processing_tasks`` rules.

    ``reclaim_stranded`` and ``claim`` encode the same predicates the SQL uses;
    the contract test below pins the real statement.
    """

    rows: dict[UUID, _Row] = field(default_factory=dict)
    slot_taken: bool = False
    reclaim_calls: int = 0

    async def acquire_worker_slot(self, **kwargs: object) -> bool:
        del kwargs
        if self.slot_taken:
            return False
        self.slot_taken = True
        return True

    async def release_worker_slot(self, **kwargs: object) -> None:
        del kwargs
        self.slot_taken = False

    async def reclaim_stranded(self, *, now: datetime) -> tuple[UUID, ...]:
        self.reclaim_calls += 1
        reclaimed: list[UUID] = []
        for row in self.rows.values():
            if row.status is not TaskStatus.RUNNING:
                continue
            if row.lease_until is None or row.lease_until > now:
                continue
            row.failure_reason = "lease expired; worker presumed crashed"
            row.lease_token = None
            row.lease_until = None
            if row.attempt_count >= row.max_attempts:
                row.status = TaskStatus.FAILED_MANUAL_REVIEW
                row.completed_at = now
            else:
                row.status = TaskStatus.RETRY_WAIT
                backoff = _BASE_DELAY_SECONDS * 2 ** max(row.attempt_count - 1, 0)
                row.available_at = now + timedelta(seconds=backoff)
                row.completed_at = None
            reclaimed.append(row.id)
        return tuple(reclaimed)

    async def claim(self, *, task_id: UUID, now: datetime, lease_token: UUID) -> bool:
        row = self.rows.get(task_id)
        if row is None:
            return False
        if row.status not in (TaskStatus.PENDING, TaskStatus.RETRY_WAIT):
            return False
        if row.available_at > now:
            return False
        if row.lease_until is not None and row.lease_until > now:
            return False
        row.status = TaskStatus.RUNNING
        row.attempt_count += 1
        row.lease_token = lease_token
        row.lease_until = now + timedelta(seconds=_BASE_DELAY_SECONDS)
        return True


@pytest.mark.asyncio
async def test_expired_running_below_max_becomes_retry_wait_and_is_later_claimable() -> None:
    repo = _ProcessingTasks(
        rows={
            TASK_A: _Row(
                id=TASK_A,
                status=TaskStatus.RUNNING,
                attempt_count=1,
                max_attempts=3,
                available_at=NOW - timedelta(minutes=10),
                lease_token=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
                lease_until=NOW - timedelta(seconds=1),
            )
        }
    )

    reclaimed = await repo.reclaim_stranded(now=NOW)

    assert reclaimed == (TASK_A,)
    row = repo.rows[TASK_A]
    assert row.status is TaskStatus.RETRY_WAIT
    assert row.lease_token is None and row.lease_until is None
    # attempt_count == 1 -> backoff = 30 * 2**0 = 30s.
    assert row.available_at == NOW + timedelta(seconds=30)

    # Still leased-out / before backoff it cannot be claimed...
    assert await repo.claim(task_id=TASK_A, now=NOW, lease_token=TASK_B) is False
    # ...but once the backoff elapses a worker can pick it up again.
    assert (
        await repo.claim(
            task_id=TASK_A, now=NOW + timedelta(seconds=30), lease_token=TASK_B
        )
        is True
    )
    assert repo.rows[TASK_A].status is TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_expired_running_at_max_attempts_becomes_failed_manual_review() -> None:
    repo = _ProcessingTasks(
        rows={
            TASK_A: _Row(
                id=TASK_A,
                status=TaskStatus.RUNNING,
                attempt_count=3,
                max_attempts=3,
                available_at=NOW - timedelta(minutes=10),
                lease_token=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
                lease_until=NOW - timedelta(seconds=1),
            )
        }
    )

    reclaimed = await repo.reclaim_stranded(now=NOW)

    assert reclaimed == (TASK_A,)
    row = repo.rows[TASK_A]
    assert row.status is TaskStatus.FAILED_MANUAL_REVIEW
    assert row.failure_reason == "lease expired; worker presumed crashed"
    assert row.completed_at == NOW
    assert row.lease_token is None and row.lease_until is None
    # A terminal task is never claimable again.
    assert (
        await repo.claim(
            task_id=TASK_A, now=NOW + timedelta(hours=1), lease_token=TASK_B
        )
        is False
    )


@pytest.mark.asyncio
async def test_running_task_with_unexpired_lease_is_not_reclaimed() -> None:
    repo = _ProcessingTasks(
        rows={
            TASK_A: _Row(
                id=TASK_A,
                status=TaskStatus.RUNNING,
                attempt_count=1,
                max_attempts=3,
                available_at=NOW - timedelta(minutes=10),
                lease_token=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
                lease_until=NOW + timedelta(seconds=5),
            )
        }
    )

    reclaimed = await repo.reclaim_stranded(now=NOW)

    assert reclaimed == ()
    assert repo.rows[TASK_A].status is TaskStatus.RUNNING
    assert repo.rows[TASK_A].lease_token is not None


@pytest.mark.asyncio
async def test_pending_and_succeeded_tasks_are_never_reclaimed() -> None:
    repo = _ProcessingTasks(
        rows={
            TASK_A: _Row(
                id=TASK_A,
                status=TaskStatus.PENDING,
                attempt_count=0,
                max_attempts=3,
                available_at=NOW - timedelta(minutes=10),
                lease_until=NOW - timedelta(seconds=1),
            ),
            TASK_B: _Row(
                id=TASK_B,
                status=TaskStatus.SUCCEEDED,
                attempt_count=1,
                max_attempts=3,
                available_at=NOW - timedelta(minutes=10),
                lease_until=NOW - timedelta(seconds=1),
            ),
        }
    )

    reclaimed = await repo.reclaim_stranded(now=NOW)

    assert reclaimed == ()
    assert repo.rows[TASK_A].status is TaskStatus.PENDING
    assert repo.rows[TASK_B].status is TaskStatus.SUCCEEDED


class _EmptyQueue:
    async def send(self, envelope: object, *, delay_seconds: int = 0) -> int:
        del envelope, delay_seconds
        return 1

    async def read_one(self, *, visibility_timeout_seconds: int) -> None:
        del visibility_timeout_seconds
        return None

    async def extend_visibility(
        self, message_id: int, *, visibility_timeout_seconds: int
    ) -> None:
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id: int) -> None:
        del message_id


@pytest.mark.asyncio
async def test_run_once_sweeps_stranded_tasks_before_reading_the_queue() -> None:
    repo = _ProcessingTasks(
        rows={
            TASK_A: _Row(
                id=TASK_A,
                status=TaskStatus.RUNNING,
                attempt_count=1,
                max_attempts=3,
                available_at=NOW - timedelta(minutes=10),
                lease_token=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
                lease_until=NOW - timedelta(seconds=1),
            )
        }
    )

    class _NeverProcess:
        async def process(self, task: object, checkpoint: object, save: object) -> None:
            raise AssertionError("no message should reach the processor")

    result = await RunWorkerOnce(
        repo, _EmptyQueue(), _NeverProcess(), clock=lambda: NOW
    ).run_once()

    # The empty queue means NO_MESSAGE, but the recovery sweep still ran first
    # and reclaimed the stranded task into a claimable RETRY_WAIT state.
    assert result.outcome is WorkerOutcome.NO_MESSAGE
    assert repo.reclaim_calls == 1
    assert repo.rows[TASK_A].status is TaskStatus.RETRY_WAIT
    assert repo.slot_taken is False


# --- Contract-style test against the real PostgresTaskRepository SQL ---------


class _RecordingCursor:
    def __init__(self, rows: list[Sequence[Any]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[Sequence[Any]]:
        return self._rows

    async def fetchone(self) -> Sequence[Any] | None:
        return self._rows[0] if self._rows else None


class _RecordingConnection:
    def __init__(self, rows: list[Sequence[Any]]) -> None:
        self._rows = rows
        self.queries: list[str] = []
        self.params: list[Sequence[object] | None] = []

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(
        self, query: str, params: Sequence[object] | None = None
    ) -> _RecordingCursor:
        self.queries.append(query)
        self.params.append(params)
        return _RecordingCursor(self._rows)


@pytest.mark.asyncio
async def test_reclaim_stranded_sql_fences_running_tasks_past_their_lease() -> None:
    connection = _RecordingConnection([(TASK_A,), (TASK_B,)])

    @contextlib.asynccontextmanager
    async def factory() -> AsyncIterator[_RecordingConnection]:
        yield connection

    repository = PostgresTaskRepository(factory)  # type: ignore[arg-type]

    reclaimed = await repository.reclaim_stranded(now=NOW)

    assert reclaimed == (TASK_A, TASK_B)
    query = connection.queries[0]
    # The WHERE fence targets exactly a crashed worker's expired RUNNING lease.
    assert "where status = 'RUNNING' and lease_until <= %s" in query
    # Same terminal-vs-backoff branch and power-of-two backoff as retry_or_fail.
    assert "'FAILED_MANUAL_REVIEW'" in query
    assert "'RETRY_WAIT'" in query
    assert "power(2, greatest(attempt_count - 1, 0))" in query
    assert "lease expired; worker presumed crashed" in query
    # ``now`` is bound to both the backoff base time and the lease fence; the
    # middle parameter is the shared retry base delay.
    assert connection.params[0] == (NOW, _BASE_DELAY_SECONDS, NOW)
