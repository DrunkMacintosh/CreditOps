"""Durable Postgres + pgvector adapter for graph-guided hybrid retrieval
(master design sections 12, 12.2, 12.3).

Every statement is scoped to a single (case_id, case_version) and filters stale
rows BEFORE ranking -- there is no code path that reaches another case or a
superseded document version:

* ``traverse_evidence_graph`` walks the typed ``evidence_edges`` with a
  ``WITH RECURSIVE`` CTE bounded by a hop counter (``hop < max_hops``) and a node
  cap (``LIMIT max_nodes``); the case+version scope and ``stale_at is null`` are
  re-asserted on EVERY hop, then the reachable nodes are resolved to candidate
  ``retrieval_passages`` (again case+version+stale scoped).
* ``lexical_search`` ranks with a Postgres full-text ``@@`` / ``ts_rank`` over the
  stored ``lexical_document`` tsvector; ``vector_search`` ranks by pgvector
  ``<=>`` distance.  Both join ``document_versions`` and require
  ``version.stale_at is null`` (document currency) as well as the passage's own
  ``stale_at is null``, and both restrict to the traversal's candidate ids.
* The QUERY embedding is a caller-supplied vector rendered to a pgvector literal
  and bound as a PARAMETER cast with ``%s::vector`` -- never string-interpolated,
  never fabricated (a missing gateway means the pipeline skips this call).
* ``hydrate_passages`` joins the ranked ids back to immutable source text +
  document/page/region and computes the sha256 ``passage_hash`` from that source.
* ``persist_query_and_hits`` appends the query row and its hit rows in ONE
  transaction.

Passage text and the query text originate in customer documents and are
UNTRUSTED: they are only ever bound as SQL parameters here, never interpreted.
Nothing on this surface can confirm a fact, satisfy a gate, or record a decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from creditops.application.ports.retrieval import (
    CandidatePassage,
    HydratedPassage,
    PersistableHit,
    ScoredPassage,
)
from creditops.domain.retrieval import SeedNodeRef
from creditops.infrastructure.postgres.repositories import ConnectionFactory


def _vector_literal(embedding: Sequence[float]) -> str:
    """Render a query embedding as a pgvector text literal.

    Mirrors ``infrastructure/postgres/document_ingestion._vector_literal``; the
    values come from the model gateway (finite floats) and are bound as a
    parameter cast ``%s::vector`` at the call site, never interpolated into SQL.
    """

    return "[" + ",".join(repr(float(value)) for value in embedding) + "]"


class PostgresEvidenceGraphRetrievalRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def traverse_evidence_graph(
        self,
        *,
        case_id: UUID,
        case_version: int,
        seeds: Sequence[SeedNodeRef],
        max_hops: int,
        max_nodes: int,
    ) -> tuple[CandidatePassage, ...]:
        # No seeds means no entry point into the graph -- return empty rather
        # than issue an invalid empty VALUES clause.
        if not seeds:
            return ()

        seed_values = ", ".join("(%s, %s)" for _ in seeds)
        seed_params: list[object] = []
        for seed in seeds:
            seed_params.append(seed.kind)
            seed_params.append(seed.node_id)

        query = f"""
            with recursive seed_nodes (entity_type, entity_id) as (
              values {seed_values}
            ),
            reachable (entity_type, entity_id, hop) as (
              select entity_type::text, entity_id::uuid, 0
              from seed_nodes
              union
              select edge.target_entity_type, edge.target_entity_id, reachable.hop + 1
              from reachable
              join public.evidence_edges as edge
                on edge.source_entity_type = reachable.entity_type
               and edge.source_entity_id = reachable.entity_id
              where edge.case_id = %s
                and edge.case_version = %s
                and edge.stale_at is null
                and reachable.hop < %s
            ),
            visited as (
              select distinct entity_type, entity_id
              from reachable
              limit %s
            )
            select distinct passage.id, passage.document_version_id, passage.page_region_id
            from public.retrieval_passages as passage
            where passage.case_id = %s
              and passage.case_version = %s
              and passage.stale_at is null
              and (
                exists (
                  select 1 from visited
                  where visited.entity_type = 'DOCUMENT_VERSION'
                    and visited.entity_id = passage.document_version_id
                )
                or exists (
                  select 1 from visited
                  where visited.entity_type = 'PAGE_REGION'
                    and visited.entity_id = passage.page_region_id
                )
                or exists (
                  select 1
                  from visited
                  join public.confirmed_facts as fact
                    on fact.id = visited.entity_id
                   and fact.case_id = passage.case_id
                   and fact.case_version = passage.case_version
                  where visited.entity_type = 'CONFIRMED_FACT'
                    and fact.page_region_id = passage.page_region_id
                )
              )
            order by passage.id
        """
        params = [
            *seed_params,
            case_id,
            case_version,
            max_hops,
            max_nodes,
            case_id,
            case_version,
        ]
        async with self._connection_factory() as connection:
            cursor = await connection.execute(query, params)
            rows = await cursor.fetchall()
        return tuple(
            CandidatePassage(
                passage_id=_uuid(row[0]),
                document_version_id=_uuid(row[1]),
                page_region_id=None if row[2] is None else _uuid(row[2]),
            )
            for row in rows
        )

    async def lexical_search(
        self,
        *,
        case_id: UUID,
        case_version: int,
        query_text: str,
        candidate_passage_ids: Sequence[UUID],
        limit: int,
    ) -> tuple[ScoredPassage, ...]:
        if not candidate_passage_ids:
            return ()
        query = """
            select passage.id,
                   ts_rank(
                     passage.lexical_document, plainto_tsquery('simple', %s)
                   ) as score
            from public.retrieval_passages as passage
            join public.document_versions as version
              on version.id = passage.document_version_id
             and version.case_id = passage.case_id
             and version.case_version = passage.case_version
            where passage.case_id = %s
              and passage.case_version = %s
              and passage.stale_at is null
              and version.stale_at is null
              and passage.id = any(%s)
              and passage.lexical_document @@ plainto_tsquery('simple', %s)
            order by score desc, passage.id
            limit %s
        """
        params = [
            query_text,
            case_id,
            case_version,
            list(candidate_passage_ids),
            query_text,
            limit,
        ]
        async with self._connection_factory() as connection:
            cursor = await connection.execute(query, params)
            rows = await cursor.fetchall()
        return tuple(
            ScoredPassage(passage_id=_uuid(row[0]), score=float(row[1]))
            for row in rows
        )

    async def vector_search(
        self,
        *,
        case_id: UUID,
        case_version: int,
        query_embedding: Sequence[float],
        candidate_passage_ids: Sequence[UUID],
        limit: int,
    ) -> tuple[ScoredPassage, ...]:
        if not candidate_passage_ids:
            return ()
        embedding_literal = _vector_literal(query_embedding)
        query = """
            select passage.id, (passage.embedding <=> %s::vector) as distance
            from public.retrieval_passages as passage
            join public.document_versions as version
              on version.id = passage.document_version_id
             and version.case_id = passage.case_id
             and version.case_version = passage.case_version
            where passage.case_id = %s
              and passage.case_version = %s
              and passage.stale_at is null
              and version.stale_at is null
              and passage.embedding is not null
              and passage.id = any(%s)
            order by passage.embedding <=> %s::vector
            limit %s
        """
        params = [
            embedding_literal,
            case_id,
            case_version,
            list(candidate_passage_ids),
            embedding_literal,
            limit,
        ]
        async with self._connection_factory() as connection:
            cursor = await connection.execute(query, params)
            rows = await cursor.fetchall()
        return tuple(
            ScoredPassage(passage_id=_uuid(row[0]), score=float(row[1]))
            for row in rows
        )

    async def hydrate_passages(
        self,
        *,
        case_id: UUID,
        case_version: int,
        passage_ids: Sequence[UUID],
    ) -> tuple[HydratedPassage, ...]:
        if not passage_ids:
            return ()
        query = """
            select passage.id, passage.case_id, passage.case_version,
                   passage.document_version_id, region.page_number,
                   passage.page_region_id, passage.passage_text,
                   encode(
                     sha256(convert_to(passage.passage_text, 'utf8')), 'hex'
                   ) as passage_hash
            from public.retrieval_passages as passage
            left join public.page_regions as region
              on region.id = passage.page_region_id
             and region.case_id = passage.case_id
             and region.case_version = passage.case_version
            where passage.case_id = %s
              and passage.case_version = %s
              and passage.stale_at is null
              and passage.id = any(%s)
        """
        params = [case_id, case_version, list(passage_ids)]
        async with self._connection_factory() as connection:
            cursor = await connection.execute(query, params)
            rows = await cursor.fetchall()
        return tuple(
            HydratedPassage(
                passage_id=_uuid(row[0]),
                case_id=_uuid(row[1]),
                case_version=int(row[2]),
                document_version_id=_uuid(row[3]),
                page_number=None if row[4] is None else int(row[4]),
                page_region_id=None if row[5] is None else _uuid(row[5]),
                passage_text=str(row[6]),
                passage_hash=str(row[7]),
            )
            for row in rows
        )

    async def persist_query_and_hits(
        self,
        *,
        case_id: UUID,
        case_version: int,
        task_id: UUID | None,
        query_text_vi: str,
        seed_node_refs: Sequence[SeedNodeRef],
        filters: Mapping[str, object],
        hits: Sequence[PersistableHit],
    ) -> UUID:
        seed_payload = [
            {"kind": seed.kind, "id": str(seed.node_id)} for seed in seed_node_refs
        ]
        query_id = uuid4()
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.retrieval_queries (
                      id, case_id, case_version, task_id, query_text_vi,
                      seed_node_refs, filters
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        query_id,
                        case_id,
                        case_version,
                        task_id,
                        query_text_vi,
                        Jsonb(seed_payload),
                        Jsonb(dict(filters)),
                    ),
                )
                row = await cursor.fetchone()
                persisted_id = _uuid(row[0]) if row is not None else query_id
                for hit in hits:
                    await connection.execute(
                        """
                        insert into public.retrieval_hits (
                          id, query_id, case_id, case_version, passage_id, rank,
                          lexical_score, vector_score, passage_hash
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            uuid4(),
                            persisted_id,
                            case_id,
                            case_version,
                            hit.passage_id,
                            hit.rank,
                            hit.lexical_score,
                            hit.vector_score,
                            hit.passage_hash,
                        ),
                    )
        return persisted_id


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
