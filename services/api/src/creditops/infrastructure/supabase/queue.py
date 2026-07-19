"""PostgreSQL/PGMQ adapter for the identifier-only processing queue."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, cast

from psycopg.types.json import Jsonb

from creditops.application.ports.queue import QueueError, QueueMessage, QueuePort
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.infrastructure.postgres.repositories import DatabaseConnection

ConnectionFactory = Callable[[], AbstractAsyncContextManager[DatabaseConnection]]

# Document-pipeline messages and agent-task messages travel on separate queues
# so orchestration work cannot starve (or be starved by) document processing.
DOCUMENT_TASK_QUEUE_NAME = "creditops_document_tasks"
AGENT_TASK_QUEUE_NAME = "creditops_agent_tasks"


class SupabaseQueue(QueuePort):
    def __init__(
        self,
        connection: DatabaseConnection | ConnectionFactory,
        *,
        queue_name: str = DOCUMENT_TASK_QUEUE_NAME,
    ) -> None:
        if not queue_name or not queue_name.replace("_", "").isalnum():
            raise ValueError("queue name must be a simple SQL identifier")
        self._connection = connection
        self._queue_name = queue_name

    @property
    def queue_name(self) -> str:
        return self._queue_name

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        if delay_seconds < 0 or delay_seconds > 86_400:
            raise QueueError("queue delay is outside the bounded contract")
        row = await self._fetchone(
            "select pgmq.send(%s, %s, %s)",
            (self._queue_name, Jsonb(envelope.model_dump(mode="json")), delay_seconds),
        )
        if row is None or not isinstance(row[0], int):
            raise QueueError("PGMQ did not return a message identifier")
        return row[0]

    async def read_one(self, *, visibility_timeout_seconds: int) -> QueueMessage | None:
        if visibility_timeout_seconds <= 0 or visibility_timeout_seconds > 86_400:
            raise QueueError("queue visibility timeout is outside the bounded contract")
        # Select the columns by NAME in an explicit order.  pgmq.read returns
        # (msg_id, read_ct, enqueued_at, vt, message[, headers]) -- ``vt`` comes
        # BEFORE ``message``.  A prior ``select *`` with positional indices read
        # ``vt`` (a timestamptz) as the message and raised "PGMQ message is not a
        # JSON object" on live pgmq (verified 2026-07-19).  Naming the columns
        # pins the contract regardless of the extension's column order.
        row = await self._fetchone(
            "select msg_id, read_ct, enqueued_at, vt, message from pgmq.read(%s, %s, %s)",
            (self._queue_name, visibility_timeout_seconds, 1),
        )
        if row is None:
            return None
        if len(row) < 5:
            raise QueueError("PGMQ read response is missing required columns")
        raw_message = row[4]
        if not isinstance(raw_message, dict):
            raise QueueError("PGMQ message is not a JSON object")
        try:
            envelope = TaskEnvelopeV1.model_validate(raw_message)
            message_id = int(row[0])
            read_count = int(row[1])
            enqueued_at = _datetime(row[2])
            visible_at = _datetime(row[3])
        except (TypeError, ValueError) as exc:
            raise QueueError("PGMQ message does not match TaskEnvelopeV1") from exc
        return QueueMessage(message_id, read_count, enqueued_at, visible_at, envelope)

    async def extend_visibility(self, message_id: int, *, visibility_timeout_seconds: int) -> None:
        if (
            message_id <= 0
            or visibility_timeout_seconds <= 0
            or visibility_timeout_seconds > 86_400
        ):
            raise QueueError("queue visibility extension is outside the bounded contract")
        # Live pgmq's set_vt signature is (queue_name text, msg_id bigint,
        # vt integer) where vt is a RELATIVE OFFSET IN SECONDS -- not an
        # absolute timestamp.  Passing a timestamptz raised UndefinedFunction
        # on every heartbeat, failing every claimed task before its processor
        # ran (live 2026-07-19).  The explicit casts also pin msg_id to bigint
        # so psycopg's small-int inference can never change the resolution.
        row = await self._fetchone(
            "select pgmq.set_vt(%s::text, %s::bigint, %s::integer)",
            (self._queue_name, message_id, visibility_timeout_seconds),
        )
        if row is not None and row[0] is False:
            raise QueueError("PGMQ visibility extension was rejected")

    async def archive(self, message_id: int) -> None:
        if message_id <= 0:
            raise QueueError("queue message identifier is invalid")
        row = await self._fetchone(
            "select pgmq.archive(%s::text, %s::bigint)",
            (self._queue_name, message_id),
        )
        if row is not None and row[0] is False:
            raise QueueError("PGMQ archive was rejected")

    async def _fetchone(
        self,
        query: str,
        params: tuple[object, ...],
    ) -> Sequence[Any] | None:
        connection = self._connection
        if hasattr(connection, "execute"):
            cursor = await cast(DatabaseConnection, connection).execute(query, params)
            return await cursor.fetchone()
        provider = connection
        async with provider() as managed_connection:
            async with managed_connection.transaction():
                cursor = await managed_connection.execute(query, params)
                return await cursor.fetchone()


def _datetime(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("PGMQ timestamp is invalid")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
