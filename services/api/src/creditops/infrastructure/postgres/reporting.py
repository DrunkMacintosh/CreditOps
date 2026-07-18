"""Durable Postgres adapter for the operations-reporting surface.

Strictly read-only aggregate assembly: ONE ``select`` per metric family, each a
``group by ... count(*)`` (or a scalar aggregate) that NEVER selects a case id,
document body, payload, or any other per-case column.  There is no
``insert`` / ``update`` / ``delete`` anywhere in this module
(``tests/contract/postgres/test_reporting_adapter.py`` proves both properties at
the captured-SQL level).  The report can therefore neither mutate state nor
reach a single case.

Age is computed IN SQL (``clock_timestamp() - available_at``) and bucketed into
PROPOSED synthetic bands, so no per-task timestamp and no clock value crosses
the boundary -- only the band label and its count.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from creditops.application.ports.reporting import (
    GateStatusCount,
    OperationsMetrics,
    OutboxBacklog,
    QueueAgeBucketCount,
    StageCount,
    StatusCount,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

# Metric family 1: processing tasks grouped by lifecycle status.
_SQL_TASKS_BY_STATUS = """
    select status, count(*) as n
    from public.processing_tasks
    group by status
    order by status
"""

# Metric family 2: queue-age buckets for the two waiting statuses only.  The
# age is ``clock_timestamp() - available_at`` computed in SQL and bucketed into
# PROPOSED synthetic bands; only (status, bucket, count) leaves the database.
_SQL_QUEUE_AGE_BUCKETS = """
    select
      status,
      case
        when clock_timestamp() - available_at < interval '1 minute'  then 'LE_1M'
        when clock_timestamp() - available_at < interval '5 minutes' then 'LE_5M'
        when clock_timestamp() - available_at < interval '15 minutes' then 'LE_15M'
        when clock_timestamp() - available_at < interval '60 minutes' then 'LE_60M'
        else 'GT_60M'
      end as age_bucket,
      count(*) as n
    from public.processing_tasks
    where status in ('PENDING', 'RETRY_WAIT')
    group by status, age_bucket
    order by status, age_bucket
"""

# Metric family 3: human gates grouped by (gate_type, status).
_SQL_GATES_BY_TYPE_STATUS = """
    select gate_type, status, count(*) as n
    from public.human_gates
    group by gate_type, status
    order by gate_type, status
"""

# Metric family 4: transactional-outbox backlog -- undispatched count and the
# max dispatch-attempt count among them.  Scalars only.
_SQL_OUTBOX_BACKLOG = """
    select count(*) as undispatched, coalesce(max(dispatch_attempts), 0) as max_attempts
    from public.outbox_events
    where dispatched_at is null
"""

# Metric family 5: current (non-stale) document versions grouped by ingestion
# stage.  ``stale_at is null`` keeps this to the live version per document.
_SQL_DOCUMENTS_BY_STAGE = """
    select stage, count(*) as n
    from public.document_versions
    where stale_at is null
    group by stage
    order by stage
"""

# Metric family 6: early-warning alerts grouped by lifecycle status.
_SQL_ALERTS_BY_STATUS = """
    select status, count(*) as n
    from public.early_warning_alerts
    group by status
    order by status
"""


class PostgresReportingRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def load_operations_metrics(self) -> OperationsMetrics:
        async with self._connection_factory() as connection:
            tasks_rows = await _rows(connection, _SQL_TASKS_BY_STATUS)
            queue_rows = await _rows(connection, _SQL_QUEUE_AGE_BUCKETS)
            gate_rows = await _rows(connection, _SQL_GATES_BY_TYPE_STATUS)
            outbox_row = await _row(connection, _SQL_OUTBOX_BACKLOG)
            document_rows = await _rows(connection, _SQL_DOCUMENTS_BY_STAGE)
            alert_rows = await _rows(connection, _SQL_ALERTS_BY_STATUS)

        return OperationsMetrics(
            tasks_by_status=tuple(
                StatusCount(status=str(row[0]), count=int(row[1])) for row in tasks_rows
            ),
            queue_age_buckets=tuple(
                QueueAgeBucketCount(
                    status=str(row[0]), bucket=str(row[1]), count=int(row[2])
                )
                for row in queue_rows
            ),
            human_gates=tuple(
                GateStatusCount(
                    gate_type=str(row[0]), status=str(row[1]), count=int(row[2])
                )
                for row in gate_rows
            ),
            outbox=OutboxBacklog(
                undispatched_count=int(outbox_row[0]) if outbox_row is not None else 0,
                max_attempts=int(outbox_row[1]) if outbox_row is not None else 0,
            ),
            documents_by_stage=tuple(
                StageCount(stage=str(row[0]), count=int(row[1])) for row in document_rows
            ),
            alerts_by_status=tuple(
                StatusCount(status=str(row[0]), count=int(row[1])) for row in alert_rows
            ),
        )


async def _rows(connection: DatabaseConnection, query: str) -> Sequence[Sequence[Any]]:
    cursor = await connection.execute(query)
    return await cursor.fetchall()


async def _row(connection: DatabaseConnection, query: str) -> Sequence[Any] | None:
    cursor = await connection.execute(query)
    return await cursor.fetchone()
