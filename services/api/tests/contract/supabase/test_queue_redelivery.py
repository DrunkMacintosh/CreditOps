from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from creditops.application.ports.queue import QueueError
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.infrastructure.supabase.queue import SupabaseQueue

NOW = datetime(2026, 7, 18, 5, 0, tzinfo=UTC)
TASK = UUID("30000000-0000-0000-0000-000000000001")
CASE = UUID("10000000-0000-0000-0000-000000000001")
DOC = UUID("20000000-0000-0000-0000-000000000001")


class Cursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    async def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class Connection:
    def __init__(self, rows: list[tuple[object, ...] | None]) -> None:
        self.rows = rows
        self.queries: list[str] = []
        self.params: list[tuple[object, ...] | None] = []

    async def execute(self, query: str, params: tuple[object, ...] | None = None) -> Cursor:
        self.queries.append(query)
        self.params.append(params)
        return Cursor(self.rows.pop(0) if self.rows else (True,))


def envelope() -> TaskEnvelopeV1:
    return TaskEnvelopeV1(
        task_id=TASK, case_id=CASE, case_version=1, document_version_id=DOC
    )


@pytest.mark.asyncio
async def test_queue_reads_without_pop_and_archives_only_explicitly() -> None:
    # Real pgmq.read column order: (msg_id, read_ct, enqueued_at, vt, message).
    connection = Connection(
        [
            (42, 1, NOW, NOW, envelope().model_dump(mode="json")),
            (True,),
        ]
    )
    queue = SupabaseQueue(connection)
    message = await queue.read_one(visibility_timeout_seconds=300)
    assert message is not None
    assert message.message_id == 42
    assert message.envelope.task_id == TASK
    assert "pgmq.read" in connection.queries[0]
    assert "pgmq.pop" not in " ".join(connection.queries).lower()
    await queue.archive(42)
    assert "pgmq.archive" in connection.queries[1]


@pytest.mark.asyncio
async def test_queue_reads_message_from_pgmq_vt_then_message_order() -> None:
    # Regression for the live crash (2026-07-19): pgmq.read returns
    # (msg_id, read_ct, enqueued_at, vt, message) -- vt BEFORE message. The
    # adapter must read the message from row[4] and vt from row[3]; reading
    # row[3] as the message surfaced "PGMQ message is not a JSON object".
    vt = datetime(2026, 7, 19, 3, 0, tzinfo=UTC)
    connection = Connection([(7, 2, NOW, vt, envelope().model_dump(mode="json"))])
    queue = SupabaseQueue(connection)
    message = await queue.read_one(visibility_timeout_seconds=300)
    assert message is not None
    assert message.message_id == 7
    assert message.read_count == 2
    assert message.visible_at == vt
    assert message.envelope.task_id == TASK
    # The query must name the columns (order-independent), never "select *".
    assert "select *" not in connection.queries[0].lower()
    assert "vt, message" in connection.queries[0].lower()


@pytest.mark.asyncio
async def test_extend_visibility_sends_relative_seconds_offset_with_casts() -> None:
    # Regression (live 2026-07-19, tasks c5b03bec/a9a6f3d1): live pgmq's
    # set_vt is (queue_name text, msg_id bigint, vt integer) where vt is a
    # RELATIVE SECONDS OFFSET.  Sending an absolute timestamptz raised
    # UndefinedFunction on every heartbeat, failing every claimed task before
    # its processor ran.
    connection = Connection([(True,)])
    queue = SupabaseQueue(connection)
    await queue.extend_visibility(7, visibility_timeout_seconds=300)
    assert "pgmq.set_vt(%s::text, %s::bigint, %s::integer)" in connection.queries[0]
    assert connection.params[0] == ("creditops_document_tasks", 7, 300)


@pytest.mark.asyncio
async def test_queue_rejects_malformed_message_and_bounds_visibility() -> None:
    connection = Connection([(42, 1, NOW, NOW, {"task_id": "not-a-uuid"})])
    queue = SupabaseQueue(connection)
    with pytest.raises(QueueError):
        await queue.read_one(visibility_timeout_seconds=300)
    with pytest.raises(QueueError):
        await queue.read_one(visibility_timeout_seconds=86_401)
