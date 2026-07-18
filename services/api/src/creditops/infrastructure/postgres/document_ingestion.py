"""Durable Postgres adapter for the document-ingestion persistence port.

Follows the repository style in ``infrastructure/postgres/repositories.py``:
a ``connection_factory`` yields a connection, work runs inside
``connection.transaction()``, and JSON columns are bound with ``Jsonb``.

Every write is idempotent on a natural key so a redelivered task creates no
duplicate rows: parsed regions, candidate facts and retrieval passages carry
deterministic primary keys and are inserted ``on conflict (id) do nothing``.
Each ``persist_*`` method fuses its inserts with the guarded, transition-checked
stage advance the completed stage implies, in one transaction — so a committed
row-write always coincides with a committed ``document_versions.stage`` advance.

The adapter writes only ``candidate_facts`` (never ``confirmed_facts``) and
``retrieval_passages``; nothing here can confirm a fact, satisfy a gate, or
record a credit decision.  Document-derived values (``proposed_value``, passage
text) are always bound as SQL parameters, never interpolated into a statement.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.document_ingestion import (
    DocumentIngestionContext,
    DocumentStageConflict,
    PersistableCandidate,
    PersistablePassage,
    PersistableRegion,
)
from creditops.domain.enums import DocumentStage
from creditops.domain.transitions import advance_document
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection


def _vector_literal(embedding: tuple[float, ...] | None) -> str | None:
    """Render an embedding as a pgvector text literal, or ``None`` for no vector.

    The vector is cast with ``%s::vector`` at the call site; the values are
    produced only by ``validate_embedding`` (finite floats), never fabricated.
    """

    if embedding is None:
        return None
    return "[" + ",".join(repr(float(value)) for value in embedding) + "]"


class PostgresDocumentIngestionRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def load_document(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
    ) -> DocumentIngestionContext | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select stage, original_filename, declared_content_type,
                       detected_content_type, storage_bucket, storage_object_key,
                       byte_size, content_sha256, stale_at
                from public.document_versions
                where id = %s and case_id = %s and case_version = %s
                """,
                (document_version_id, case_id, case_version),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return DocumentIngestionContext(
            case_id=case_id,
            case_version=case_version,
            document_version_id=document_version_id,
            stage=DocumentStage(str(row[0])),
            original_filename=str(row[1]),
            declared_content_type=str(row[2]),
            detected_content_type=None if row[3] is None else str(row[3]),
            storage_bucket=str(row[4]),
            storage_object_key=str(row[5]),
            byte_size=int(cast(int, row[6])),
            content_sha256=str(row[7]),
            is_stale=row[8] is not None,
        )

    async def persist_parsed_regions(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        regions: Sequence[PersistableRegion],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                for region in regions:
                    await connection.execute(
                        """
                        insert into public.page_regions (
                          id, case_id, case_version, document_version_id,
                          page_number, x, y, width, height, extraction_method
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (id) do nothing
                        """,
                        (
                            region.page_region_id,
                            case_id,
                            case_version,
                            document_version_id,
                            region.page_number,
                            region.x,
                            region.y,
                            region.width,
                            region.height,
                            region.extraction_method,
                        ),
                    )
                await self._advance_stage(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    document_version_id=document_version_id,
                    from_stage=from_stage,
                    to_stage=to_stage,
                )

    async def persist_candidates(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        candidates: Sequence[PersistableCandidate],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                for candidate in candidates:
                    await connection.execute(
                        """
                        insert into public.candidate_facts (
                          id, case_id, case_version, document_version_id,
                          page_region_id, field_key, proposed_value, confidence,
                          extraction_method
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (id) do nothing
                        """,
                        (
                            candidate.candidate_fact_id,
                            case_id,
                            case_version,
                            document_version_id,
                            candidate.page_region_id,
                            candidate.field_key,
                            Jsonb(candidate.proposed_value),
                            candidate.confidence,
                            candidate.extraction_method,
                        ),
                    )
                await self._advance_stage(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    document_version_id=document_version_id,
                    from_stage=from_stage,
                    to_stage=to_stage,
                )

    async def persist_passages(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        passages: Sequence[PersistablePassage],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                for passage in passages:
                    await connection.execute(
                        """
                        insert into public.retrieval_passages (
                          id, case_id, case_version, document_version_id,
                          page_region_id, passage_text, extraction_method,
                          embedding, embedding_model, embedding_version
                        ) values (
                          %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s
                        )
                        on conflict (id) do nothing
                        """,
                        (
                            passage.passage_id,
                            case_id,
                            case_version,
                            document_version_id,
                            passage.page_region_id,
                            passage.passage_text,
                            passage.extraction_method,
                            _vector_literal(passage.embedding),
                            passage.embedding_model,
                            passage.embedding_version,
                        ),
                    )
                await self._advance_stage(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    document_version_id=document_version_id,
                    from_stage=from_stage,
                    to_stage=to_stage,
                )

    async def advance_stage(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await self._advance_stage(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    document_version_id=document_version_id,
                    from_stage=from_stage,
                    to_stage=to_stage,
                )

    @staticmethod
    async def _advance_stage(
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        # Fail closed in-process if the pair is not a legal single-step move.
        advance_document(from_stage, to_stage)
        # Idempotent guarded compare-and-set: the update matches the row only
        # when it is still at ``from`` (advance) or already at ``to`` (a redelivery
        # that already advanced).  No matching row means the version is in an
        # unexpected stage, which fails closed rather than skipping the advance.
        cursor = await connection.execute(
            """
            update public.document_versions
            set stage = %s, updated_at = clock_timestamp()
            where id = %s and case_id = %s and case_version = %s
              and stage in (%s, %s)
            returning stage
            """,
            (
                to_stage.value,
                document_version_id,
                case_id,
                case_version,
                from_stage.value,
                to_stage.value,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise DocumentStageConflict(
                f"document version {document_version_id} was not in stage "
                f"{from_stage.value} for the advance to {to_stage.value}"
            )
