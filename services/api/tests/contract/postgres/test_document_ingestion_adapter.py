"""Contract tests for the Postgres document-ingestion persistence adapter.

Mirrors ``tests/contract/supabase/test_queue_redelivery.py``: a fake connection
captures the exact SQL and parameters the adapter issues, so these tests prove
the transactional shape and the idempotent ``on conflict`` clauses without a
live Postgres instance.  Every write the ingestion processor performs must be
idempotent on a natural key so a redelivered task creates no duplicate rows.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from uuid import UUID

import pytest

from creditops.application.ports.document_ingestion import (
    DocumentStageConflict,
    PersistableCandidate,
    PersistablePassage,
    PersistableRegion,
)
from creditops.domain.enums import DocumentStage
from creditops.infrastructure.postgres.document_ingestion import (
    PostgresDocumentIngestionRepository,
)

CASE = UUID("10000000-0000-0000-0000-000000000001")
DOC = UUID("20000000-0000-0000-0000-000000000001")
REGION = UUID("40000000-0000-0000-0000-000000000001")
CANDIDATE = UUID("50000000-0000-0000-0000-000000000001")
PASSAGE = UUID("60000000-0000-0000-0000-000000000001")


class Cursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    async def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class Transaction(AbstractAsyncContextManager[None]):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        self._connection.transaction_depth += 1
        self._connection.transactions_opened += 1
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._connection.transaction_depth -= 1


class Connection:
    def __init__(self, rows: list[tuple[object, ...] | None] | None = None) -> None:
        self.rows = list(rows or [])
        self.queries: list[str] = []
        self.params: list[tuple[object, ...] | None] = []
        self.transaction_depth = 0
        self.transactions_opened = 0
        self.executed_in_transaction: list[bool] = []

    def transaction(self) -> Transaction:
        return Transaction(self)

    async def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> Cursor:
        self.queries.append(query)
        self.params.append(params)
        self.executed_in_transaction.append(self.transaction_depth > 0)
        return Cursor(self.rows.pop(0) if self.rows else (True,))


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


def _repo(connection: Connection) -> PostgresDocumentIngestionRepository:
    return PostgresDocumentIngestionRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


@pytest.mark.asyncio
async def test_persist_regions_is_transactional_and_idempotent() -> None:
    connection = Connection()
    repo = _repo(connection)

    await repo.persist_parsed_regions(
        case_id=CASE,
        case_version=1,
        document_version_id=DOC,
        regions=[
            PersistableRegion(
                page_region_id=REGION,
                page_number=1,
                x=0.0,
                y=0.0,
                width=1.0,
                height=0.1,
                extraction_method="deterministic-parser-v1",
            )
        ],
        from_stage=DocumentStage.SECURITY_VALIDATED,
        to_stage=DocumentStage.PARSED,
    )

    sql = _sql(connection)
    assert "insert into public.page_regions" in sql
    assert "on conflict (id) do nothing" in sql
    assert "update public.document_versions" in sql
    # Both the row insert and the guarded stage advance run inside one transaction.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


@pytest.mark.asyncio
async def test_persist_candidates_uses_on_conflict_and_advances_stage() -> None:
    connection = Connection()
    repo = _repo(connection)

    await repo.persist_candidates(
        case_id=CASE,
        case_version=1,
        document_version_id=DOC,
        candidates=[
            PersistableCandidate(
                candidate_fact_id=CANDIDATE,
                page_region_id=REGION,
                field_key="requested_amount",
                proposed_value="500000000",
                confidence=0.9,
                extraction_method="fpt-kie",
            )
        ],
        from_stage=DocumentStage.CLASSIFIED,
        to_stage=DocumentStage.EXTRACTED,
    )

    sql = _sql(connection)
    assert "insert into public.candidate_facts" in sql
    assert "on conflict (id) do nothing" in sql
    assert "update public.document_versions" in sql
    assert connection.transactions_opened == 1
    # The proposed value is bound as a parameter, never interpolated into SQL.
    insert_index = next(
        i for i, q in enumerate(connection.queries) if "candidate_facts" in q.lower()
    )
    assert "500000000" not in connection.queries[insert_index]
    assert connection.params[insert_index] is not None
    assert "500000000" in str(connection.params[insert_index])


@pytest.mark.asyncio
async def test_persist_passages_is_transactional_and_idempotent() -> None:
    connection = Connection()
    repo = _repo(connection)

    await repo.persist_passages(
        case_id=CASE,
        case_version=1,
        document_version_id=DOC,
        passages=[
            PersistablePassage(
                passage_id=PASSAGE,
                page_region_id=REGION,
                passage_text="So tien de nghi: 500000000",
                extraction_method="deterministic-parser-v1",
                embedding=(0.1, 0.2),
                embedding_model="embedding-gated-v1",
                embedding_version="r1",
            )
        ],
        from_stage=DocumentStage.EXTRACTED,
        to_stage=DocumentStage.INDEXED,
    )

    sql = _sql(connection)
    assert "insert into public.retrieval_passages" in sql
    assert "on conflict (id) do nothing" in sql
    assert "update public.document_versions" in sql
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)


@pytest.mark.asyncio
async def test_advance_stage_is_a_guarded_compare_and_set() -> None:
    connection = Connection()
    repo = _repo(connection)

    await repo.advance_stage(
        case_id=CASE,
        case_version=1,
        document_version_id=DOC,
        from_stage=DocumentStage.REGISTERED,
        to_stage=DocumentStage.SECURITY_VALIDATED,
    )

    sql = _sql(connection)
    assert "update public.document_versions" in sql
    assert "set stage" in sql
    assert "where" in sql
    # The guard binds the case scope and both accepted stages as parameters.
    params = connection.params[0]
    assert params is not None
    assert CASE in params
    assert DOC in params
    assert DocumentStage.SECURITY_VALIDATED.value in params
    assert DocumentStage.REGISTERED.value in params


@pytest.mark.asyncio
async def test_advance_stage_conflict_when_no_row_matches() -> None:
    # A guarded advance that updates no row (unexpected persisted stage) fails
    # closed instead of silently skipping the transition.
    connection = Connection(rows=[None])
    repo = _repo(connection)

    with pytest.raises(DocumentStageConflict):
        await repo.advance_stage(
            case_id=CASE,
            case_version=1,
            document_version_id=DOC,
            from_stage=DocumentStage.REGISTERED,
            to_stage=DocumentStage.SECURITY_VALIDATED,
        )


@pytest.mark.asyncio
async def test_advance_stage_rejects_an_illegal_transition() -> None:
    connection = Connection()
    repo = _repo(connection)

    with pytest.raises(ValueError):
        await repo.advance_stage(
            case_id=CASE,
            case_version=1,
            document_version_id=DOC,
            from_stage=DocumentStage.REGISTERED,
            to_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
        )


@pytest.mark.asyncio
async def test_load_document_reads_version_scope() -> None:
    connection = Connection(
        rows=[
            (
                "REGISTERED",
                "don.pdf",
                "application/pdf",
                "application/pdf",
                "creditops-originals",
                f"originals/{CASE}/intent",
                len(b"%PDF-1.7"),
                "a" * 64,
                None,
            )
        ]
    )
    repo = _repo(connection)

    context = await repo.load_document(
        case_id=CASE, case_version=1, document_version_id=DOC
    )

    assert context is not None
    assert context.stage is DocumentStage.REGISTERED
    assert context.is_stale is False
    sql = _sql(connection)
    assert "from public.document_versions" in sql
    params = connection.params[0]
    assert params is not None
    assert CASE in params and DOC in params
