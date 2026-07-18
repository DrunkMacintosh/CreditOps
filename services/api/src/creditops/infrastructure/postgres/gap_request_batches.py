"""Durable Postgres adapter for the pre-Risk gap-request workflow (G2).

Every write here is bounded and append-only: an idempotent batch insert
deduplicated on ``(case, version, open-gap snapshot hash)`` with its embedded
items, and append-only human dispositions.  Nothing here resolves a gap,
mutates a batch, or satisfies a gate -- gate satisfaction is derived
(``domain/gap_request_batches.derive_g2_from_batch``) and written only through
the orchestration repository by ``api/gap_requests.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.gap_requests import (
    GapRequestBatchDispositionRecord,
    OpenGap,
    PersistedGapRequestBatch,
)
from creditops.domain.gap_request_batches import (
    BatchDispositionType,
    GapRequestBatch,
    GapRequestItem,
    ItemDisposition,
)
from creditops.domain.underwriting import GapBlockingLevel
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_OPEN_GAP_STATUSES = ("PROVISIONAL", "FORMAL")


class PostgresGapRequestRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def load_open_gaps(
        self, case_id: UUID, case_version: int
    ) -> tuple[OpenGap, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, status, blocking_level, missing_information_vi,
                       suggested_evidence_vi
                from public.evidence_gaps
                where case_id = %s and case_version = %s
                  and status in ('PROVISIONAL', 'FORMAL')
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(
            OpenGap(
                gap_id=cast(UUID, row[0]),
                status=str(row[1]),
                blocking_level=GapBlockingLevel(str(row[2])),
                missing_information_vi=str(row[3]),
                suggested_evidence_vi=tuple(str(item) for item in (row[4] or [])),
            )
            for row in rows
        )

    async def load_current_batch(
        self, case_id: UUID, case_version: int
    ) -> GapRequestBatch | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, open_gap_snapshot_hash
                from public.gap_request_batches
                where case_id = %s and case_version = %s
                order by created_at desc
                limit 1
                """,
                (case_id, case_version),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            batch_id = cast(UUID, row[0])
            snapshot_hash = str(row[1])
            items = await self._load_items(connection, batch_id)
        return GapRequestBatch(
            id=batch_id,
            case_id=case_id,
            case_version=case_version,
            items=items,
            open_gap_snapshot_hash=snapshot_hash,
        )

    @staticmethod
    async def _load_items(
        connection: DatabaseConnection, batch_id: UUID
    ) -> tuple[GapRequestItem, ...]:
        cursor = await connection.execute(
            """
            select id, gap_id, request_text_vi, blocking_level
            from public.gap_request_items
            where batch_id = %s
            order by created_at, id
            """,
            (batch_id,),
        )
        rows = await cursor.fetchall()
        return tuple(
            GapRequestItem(
                id=cast(UUID, row[0]),
                gap_id=cast(UUID, row[1]),
                request_text_vi=str(row[2]),
                blocking_level=GapBlockingLevel(str(row[3])),
            )
            for row in rows
        )

    async def persist_batch(self, batch: GapRequestBatch) -> PersistedGapRequestBatch:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.gap_request_batches (
                      id, case_id, case_version, open_gap_snapshot_hash
                    ) values (%s, %s, %s, %s)
                    on conflict (case_id, case_version, open_gap_snapshot_hash)
                      do nothing
                    returning id
                    """,
                    (
                        batch.id,
                        batch.case_id,
                        batch.case_version,
                        batch.open_gap_snapshot_hash,
                    ),
                )
                inserted = await cursor.fetchone()
                created = inserted is not None
                if created:
                    for item in batch.items:
                        await connection.execute(
                            """
                            insert into public.gap_request_items (
                              id, batch_id, case_id, case_version, gap_id,
                              request_text_vi, blocking_level
                            ) values (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                item.id,
                                batch.id,
                                batch.case_id,
                                batch.case_version,
                                item.gap_id,
                                item.request_text_vi,
                                item.blocking_level.value,
                            ),
                        )
                stored = await self._load_batch_by_key(
                    connection,
                    batch.case_id,
                    batch.case_version,
                    batch.open_gap_snapshot_hash,
                )
        if stored is None:
            raise RuntimeError("gap-request batch idempotency row disappeared")
        return PersistedGapRequestBatch(batch=stored, created=created)

    @staticmethod
    async def _load_batch_by_key(
        connection: DatabaseConnection,
        case_id: UUID,
        case_version: int,
        snapshot_hash: str,
    ) -> GapRequestBatch | None:
        cursor = await connection.execute(
            """
            select id from public.gap_request_batches
            where case_id = %s and case_version = %s
              and open_gap_snapshot_hash = %s
            """,
            (case_id, case_version, snapshot_hash),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        batch_id = cast(UUID, row[0])
        items = await PostgresGapRequestRepository._load_items(connection, batch_id)
        return GapRequestBatch(
            id=batch_id,
            case_id=case_id,
            case_version=case_version,
            items=items,
            open_gap_snapshot_hash=snapshot_hash,
        )

    async def record_disposition(
        self,
        *,
        disposition_id: UUID,
        batch_id: UUID,
        case_id: UUID,
        case_version: int,
        disposition_type: BatchDispositionType,
        item_dispositions: Mapping[UUID, ItemDisposition],
        edited_texts: Mapping[UUID, str],
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> GapRequestBatchDispositionRecord:
        item_map = {str(k): v for k, v in item_dispositions.items()}
        edited_map = {str(k): v for k, v in edited_texts.items()}
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.gap_request_batch_dispositions (
                      id, batch_id, case_id, case_version, disposition_type,
                      item_dispositions, edited_texts, actor_id, actor_role,
                      rationale_vi
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        disposition_id,
                        batch_id,
                        case_id,
                        case_version,
                        disposition_type.value,
                        Jsonb(item_map),
                        Jsonb(edited_map),
                        actor_id,
                        actor_role,
                        rationale_vi,
                    ),
                )
                created = await cursor.fetchone()
        created_at = cast(datetime, created[0]) if created is not None else datetime.now(UTC)
        return _to_record(
            disposition_id=disposition_id,
            batch_id=batch_id,
            disposition_type=disposition_type.value,
            item_map=item_map,
            edited_map=edited_map,
            actor_id=actor_id,
            actor_role=actor_role,
            rationale_vi=rationale_vi,
            created_at=created_at,
        )

    async def load_dispositions(
        self, batch_id: UUID
    ) -> tuple[GapRequestBatchDispositionRecord, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, batch_id, disposition_type, item_dispositions,
                       edited_texts, actor_id, actor_role, rationale_vi, created_at
                from public.gap_request_batch_dispositions
                where batch_id = %s
                order by created_at, id
                """,
                (batch_id,),
            )
            rows = await cursor.fetchall()
        return tuple(
            _to_record(
                disposition_id=cast(UUID, row[0]),
                batch_id=cast(UUID, row[1]),
                disposition_type=str(row[2]),
                item_map={str(k): str(v) for k, v in (row[3] or {}).items()},
                edited_map={str(k): str(v) for k, v in (row[4] or {}).items()},
                actor_id=cast(UUID, row[5]),
                actor_role=str(row[6]),
                rationale_vi=str(row[7]),
                created_at=cast(datetime, row[8]),
            )
            for row in rows
        )


def _to_record(
    *,
    disposition_id: UUID,
    batch_id: UUID,
    disposition_type: str,
    item_map: Mapping[str, str],
    edited_map: Mapping[str, str],
    actor_id: UUID,
    actor_role: str,
    rationale_vi: str,
    created_at: datetime,
) -> GapRequestBatchDispositionRecord:
    return GapRequestBatchDispositionRecord(
        id=disposition_id,
        batch_id=batch_id,
        disposition_type=BatchDispositionType(disposition_type),
        item_dispositions={
            UUID(k): cast(ItemDisposition, v) for k, v in item_map.items()
        },
        edited_texts={UUID(k): v for k, v in edited_map.items()},
        actor_id=actor_id,
        actor_role=actor_role,
        rationale_vi=rationale_vi,
        created_at=created_at,
    )
