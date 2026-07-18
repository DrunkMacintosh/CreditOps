"""Contract tests for the Postgres + pgvector retrieval adapter.

A fake connection captures the exact SQL and parameters the adapter issues so we
can prove -- without a live Postgres -- that:

* ``traverse_evidence_graph`` uses a ``WITH RECURSIVE`` CTE bounded by the hop
  counter and the node-count ``LIMIT`` (both passed as parameters);
* EVERY read is scoped to the case + version and filters ``stale_at is null``
  (plus document-version currency for the ranking legs);
* ``vector_search`` binds the query embedding as a parameter cast ``%s::vector``
  (never string-interpolated);
* ``hydrate_passages`` computes the sha256 passage hash from the immutable
  source text; and
* ``persist_query_and_hits`` writes the query row and its hit rows in ONE
  transaction.

All identifiers are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from uuid import UUID

import pytest

from creditops.application.ports.retrieval import PersistableHit
from creditops.domain.retrieval import SeedNodeRef
from creditops.infrastructure.postgres.retrieval import (
    PostgresEvidenceGraphRetrievalRepository,
)

CASE = UUID("10000000-0000-0000-0000-0000000000f1")
CASE_VERSION = 2
DOC = UUID("61000000-0000-0000-0000-0000000000f1")
REGION = UUID("62000000-0000-0000-0000-0000000000f1")
PASSAGE = UUID("63000000-0000-0000-0000-0000000000f1")
QUERY_ID = UUID("70000000-0000-0000-0000-0000000000f1")
MAX_HOPS = 3
MAX_NODES = 200
HASH = "a" * 64


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
        self.params: list[tuple[object, ...] | list[object] | None] = []
        self.transaction_depth = 0
        self.transactions_opened = 0
        self.executed_in_transaction: list[bool] = []

    def transaction(self) -> Transaction:
        return Transaction(self)

    async def execute(
        self, query: str, params: tuple[object, ...] | list[object] | None = None
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


def _repo(connection: Connection) -> PostgresEvidenceGraphRetrievalRepository:
    return PostgresEvidenceGraphRetrievalRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _flat_params(connection: Connection) -> list[object]:
    flat: list[object] = []
    for params in connection.params:
        if params is not None:
            flat.extend(params)
    return flat


# -- traverse -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_traverse_uses_bounded_recursive_cte() -> None:
    connection = Connection(results=[[(PASSAGE, DOC, REGION)]])
    repo = _repo(connection)

    candidates = await repo.traverse_evidence_graph(
        case_id=CASE,
        case_version=CASE_VERSION,
        seeds=(SeedNodeRef(kind="CONFIRMED_FACT", node_id=PASSAGE),),
        max_hops=MAX_HOPS,
        max_nodes=MAX_NODES,
    )

    assert candidates[0].passage_id == PASSAGE
    sql = _sql(connection)
    assert "with recursive" in sql
    assert "reachable.hop <" in sql  # hop counter bound
    assert "limit" in sql  # node-count cap
    assert "edge.case_id = %s" in sql and "edge.case_version = %s" in sql
    assert "edge.stale_at is null" in sql
    assert "passage.stale_at is null" in sql
    # The hop and node bounds are parameters, not literals.
    params = _flat_params(connection)
    assert MAX_HOPS in params
    assert MAX_NODES in params


@pytest.mark.asyncio
async def test_traverse_without_seeds_issues_no_sql() -> None:
    connection = Connection()
    repo = _repo(connection)

    candidates = await repo.traverse_evidence_graph(
        case_id=CASE,
        case_version=CASE_VERSION,
        seeds=(),
        max_hops=MAX_HOPS,
        max_nodes=MAX_NODES,
    )

    assert candidates == ()
    assert connection.queries == []


# -- lexical + vector search --------------------------------------------------


@pytest.mark.asyncio
async def test_lexical_search_is_scoped_and_current() -> None:
    connection = Connection(results=[[(PASSAGE, 0.42)]])
    repo = _repo(connection)

    hits = await repo.lexical_search(
        case_id=CASE,
        case_version=CASE_VERSION,
        query_text="doanh thu thuan",
        candidate_passage_ids=(PASSAGE,),
        limit=50,
    )

    assert hits[0].passage_id == PASSAGE
    assert hits[0].score == 0.42
    sql = _sql(connection)
    assert "plainto_tsquery" in sql and "@@" in sql
    assert "passage.case_id = %s" in sql and "passage.case_version = %s" in sql
    assert "passage.stale_at is null" in sql
    assert "version.stale_at is null" in sql  # document-version currency
    assert "any(%s)" in sql


@pytest.mark.asyncio
async def test_lexical_search_short_circuits_without_candidates() -> None:
    connection = Connection()
    repo = _repo(connection)

    hits = await repo.lexical_search(
        case_id=CASE,
        case_version=CASE_VERSION,
        query_text="x",
        candidate_passage_ids=(),
        limit=50,
    )

    assert hits == ()
    assert connection.queries == []


@pytest.mark.asyncio
async def test_vector_search_binds_embedding_as_parameter() -> None:
    connection = Connection(results=[[(PASSAGE, 0.12)]])
    repo = _repo(connection)

    hits = await repo.vector_search(
        case_id=CASE,
        case_version=CASE_VERSION,
        query_embedding=(0.1, 0.2, 0.3),
        candidate_passage_ids=(PASSAGE,),
        limit=50,
    )

    assert hits[0].passage_id == PASSAGE
    sql = _sql(connection)
    assert "%s::vector" in sql
    assert "<=>" in sql
    assert "passage.stale_at is null" in sql
    assert "version.stale_at is null" in sql
    assert "passage.embedding is not null" in sql
    # The embedding travels as a bound pgvector literal parameter, never inlined.
    params = _flat_params(connection)
    literals = [p for p in params if isinstance(p, str) and p.startswith("[")]
    assert literals and literals[0] == "[0.1,0.2,0.3]"


# -- hydrate ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hydrate_returns_source_and_computes_hash() -> None:
    connection = Connection(
        results=[[(PASSAGE, CASE, CASE_VERSION, DOC, 1, REGION, "Doanh thu.", HASH)]]
    )
    repo = _repo(connection)

    hydrated = await repo.hydrate_passages(
        case_id=CASE, case_version=CASE_VERSION, passage_ids=(PASSAGE,)
    )

    assert hydrated[0].passage_text == "Doanh thu."
    assert hydrated[0].passage_hash == HASH
    assert hydrated[0].page_number == 1
    sql = _sql(connection)
    assert "sha256" in sql and "convert_to" in sql and "encode" in sql
    assert "passage.case_id = %s" in sql and "passage.case_version = %s" in sql
    assert "passage.stale_at is null" in sql
    assert "any(%s)" in sql


# -- every read is case+version+stale scoped ----------------------------------


@pytest.mark.asyncio
async def test_every_read_query_is_case_version_and_stale_scoped() -> None:
    for run in (
        lambda repo: repo.traverse_evidence_graph(
            case_id=CASE,
            case_version=CASE_VERSION,
            seeds=(SeedNodeRef(kind="DOCUMENT_VERSION", node_id=DOC),),
            max_hops=MAX_HOPS,
            max_nodes=MAX_NODES,
        ),
        lambda repo: repo.lexical_search(
            case_id=CASE,
            case_version=CASE_VERSION,
            query_text="x",
            candidate_passage_ids=(PASSAGE,),
            limit=10,
        ),
        lambda repo: repo.vector_search(
            case_id=CASE,
            case_version=CASE_VERSION,
            query_embedding=(0.5,),
            candidate_passage_ids=(PASSAGE,),
            limit=10,
        ),
        lambda repo: repo.hydrate_passages(
            case_id=CASE, case_version=CASE_VERSION, passage_ids=(PASSAGE,)
        ),
    ):
        connection = Connection(results=[[]])
        await run(_repo(connection))
        sql = _sql(connection)
        assert "case_id = %s" in sql
        assert "case_version = %s" in sql
        assert "stale_at is null" in sql


# -- persistence --------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_writes_query_and_hits_in_one_transaction() -> None:
    # query insert -> returns the query id; each hit insert -> no row.
    connection = Connection(results=[[(QUERY_ID,)], [], []])
    repo = _repo(connection)

    persisted = await repo.persist_query_and_hits(
        case_id=CASE,
        case_version=CASE_VERSION,
        task_id=None,
        query_text_vi="doanh thu thuan",
        seed_node_refs=(SeedNodeRef(kind="GAP", node_id=REGION),),
        filters={"mode": "HYBRID"},
        hits=(
            PersistableHit(
                passage_id=PASSAGE,
                rank=1,
                lexical_score=0.42,
                vector_score=0.87,
                passage_hash=HASH,
            ),
            PersistableHit(
                passage_id=DOC,
                rank=2,
                lexical_score=None,
                vector_score=0.55,
                passage_hash="b" * 64,
            ),
        ),
    )

    assert persisted == QUERY_ID
    sql = _sql(connection)
    assert "insert into public.retrieval_queries" in sql
    assert "insert into public.retrieval_hits" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    # One query insert + two hit inserts.
    assert sql.count("insert into public.retrieval_hits") == 2


@pytest.mark.asyncio
async def test_persist_with_no_hits_still_records_the_query() -> None:
    connection = Connection(results=[[(QUERY_ID,)]])
    repo = _repo(connection)

    persisted = await repo.persist_query_and_hits(
        case_id=CASE,
        case_version=CASE_VERSION,
        task_id=None,
        query_text_vi="x",
        seed_node_refs=(),
        filters={},
        hits=(),
    )

    assert persisted == QUERY_ID
    sql = _sql(connection)
    assert "insert into public.retrieval_queries" in sql
    assert "insert into public.retrieval_hits" not in sql
    assert connection.transactions_opened == 1
