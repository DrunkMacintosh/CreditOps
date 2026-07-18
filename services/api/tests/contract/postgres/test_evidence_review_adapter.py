"""Contract tests for the Postgres evidence-review persistence adapter.

Mirrors ``tests/contract/postgres/test_intake_adapter.py``: a fake connection
captures the exact SQL and parameters the adapter issues, proving the
single-transaction confirmation batch (fact_confirmations -> derived
confirmed_facts -> audit), the per-candidate idempotency guard, the stale/
superseded fail-closed, and the version-scoped reads -- all without a live
Postgres.  Every identifier here is synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

import pytest

from creditops.application.ports.evidence_review import (
    ConfirmationInput,
    StaleDocumentVersionError,
)
from creditops.domain.enums import DocumentStage, FactDisposition
from creditops.infrastructure.postgres.evidence_review import (
    PostgresEvidenceReviewRepository,
)

CASE = UUID("10000000-0000-0000-0000-000000000004")
CASE_VERSION = 2
DOCUMENT = UUID("20000000-0000-0000-0000-000000000004")
DOC_VERSION_ID = UUID("21000000-0000-0000-0000-000000000004")
CANDIDATE = UUID("22000000-0000-0000-0000-000000000004")
CONFIRMATION = UUID("23000000-0000-0000-0000-000000000004")
CONFIRMED_FACT = UUID("24000000-0000-0000-0000-000000000004")
PAGE_REGION = UUID("25000000-0000-0000-0000-000000000004")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
READY = "READY_FOR_OFFICER_REVIEW"
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


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


def _repo(connection: Connection) -> PostgresEvidenceReviewRepository:
    return PostgresEvidenceReviewRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


def _accepted(candidate: UUID = CANDIDATE) -> ConfirmationInput:
    return ConfirmationInput(candidate_id=candidate, disposition=FactDisposition.ACCEPTED)


# --- record_confirmations --------------------------------------------------


@pytest.mark.asyncio
async def test_confirmation_batch_is_one_transaction_and_derives_and_audits() -> None:
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],  # version lock, not stale
            [(CONFIRMATION,)],  # fact_confirmations insert returning id
            # confirmed_facts insert returning id + derived FK targets
            [(CONFIRMED_FACT, CANDIDATE, PAGE_REGION, DOC_VERSION_ID)],
            [],  # DERIVED_FROM_CANDIDATE edge
            [],  # LOCATED_IN_REGION edge
            [],  # SOURCED_FROM_DOCUMENT_VERSION edge
            [],  # audit insert (no returning)
        ]
    )
    repo = _repo(connection)

    result = await repo.record_confirmations(
        document_version_id=DOC_VERSION_ID,
        confirmations=(_accepted(),),
        actor_id=OFFICER,
        expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
    )

    assert result.created is True
    assert result.confirmation_ids == (CONFIRMATION,)
    assert result.confirmed_fact_ids == (CONFIRMED_FACT,)

    sql = _sql(connection)
    assert "insert into public.fact_confirmations" in sql
    assert "insert into public.confirmed_facts" in sql
    assert "insert into public.audit_events" in sql
    # The whole batch runs inside exactly one transaction.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    # The version is locked and server time keeps the authority timestamps
    # ordered without any client clock.
    assert "for update" in sql
    assert "now()" in sql
    # Idempotency guards on both the confirmation and the derived fact.
    assert "on conflict (candidate_fact_id) do nothing" in sql
    assert "on conflict (confirmation_id) do nothing" in sql
    # Identifiers are bound as parameters, never interpolated.
    fc_index = next(
        i
        for i, q in enumerate(connection.queries)
        if "insert into public.fact_confirmations" in q.lower()
    )
    fc_params = connection.params[fc_index]
    assert fc_params is not None
    assert CANDIDATE in fc_params
    assert OFFICER in fc_params  # actor_id AND assigned_officer_id
    assert CASE in fc_params
    assert "ACCEPTED" in fc_params
    # The audit event is the human intake officer's, keyed on the confirmation.
    audit_index = next(
        i
        for i, q in enumerate(connection.queries)
        if "insert into public.audit_events" in q.lower()
    )
    audit_params = connection.params[audit_index]
    assert audit_params is not None
    assert "HUMAN:INTAKE_OFFICER" in audit_params
    assert OFFICER in audit_params
    assert CONFIRMATION in audit_params


@pytest.mark.asyncio
async def test_confirmation_is_idempotent_on_conflict_and_skips_derive_and_audit() -> None:
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],  # version lock
            [],  # fact_confirmations insert -> conflict, nothing returned
            [(CONFIRMATION,)],  # reselect existing confirmation id
        ]
    )
    repo = _repo(connection)

    result = await repo.record_confirmations(
        document_version_id=DOC_VERSION_ID,
        confirmations=(_accepted(),),
        actor_id=OFFICER,
        expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
    )

    assert result.created is False
    assert result.confirmation_ids == (CONFIRMATION,)
    assert result.confirmed_fact_ids == ()
    sql = _sql(connection)
    # A repeat submission re-derives nothing and re-audits nothing.
    assert "insert into public.confirmed_facts" not in sql
    assert "insert into public.audit_events" not in sql
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_corrected_confirmation_binds_corrected_value_and_derives_fact() -> None:
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],
            [(CONFIRMATION,)],
            [(CONFIRMED_FACT, CANDIDATE, PAGE_REGION, DOC_VERSION_ID)],
            [],  # DERIVED_FROM_CANDIDATE edge
            [],  # LOCATED_IN_REGION edge
            [],  # SOURCED_FROM_DOCUMENT_VERSION edge
            [],  # audit insert
        ]
    )
    repo = _repo(connection)

    result = await repo.record_confirmations(
        document_version_id=DOC_VERSION_ID,
        confirmations=(
            ConfirmationInput(
                candidate_id=CANDIDATE,
                disposition=FactDisposition.CORRECTED,
                corrected_value="4500000000",
                rationale="OCR misread",
            ),
        ),
        actor_id=OFFICER,
        expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
    )

    assert result.confirmed_fact_ids == (CONFIRMED_FACT,)
    sql = _sql(connection)
    assert "insert into public.confirmed_facts" in sql
    fc_index = next(
        i
        for i, q in enumerate(connection.queries)
        if "insert into public.fact_confirmations" in q.lower()
    )
    fc_params = connection.params[fc_index]
    assert fc_params is not None
    assert "CORRECTED" in fc_params
    # corrected_value is wrapped as jsonb (Jsonb), not a bare python str.
    assert "4500000000" not in fc_params


@pytest.mark.asyncio
async def test_absent_disposition_records_confirmation_but_no_confirmed_fact() -> None:
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],
            [(CONFIRMATION,)],  # fact_confirmations insert
            [],  # audit insert
        ]
    )
    repo = _repo(connection)

    result = await repo.record_confirmations(
        document_version_id=DOC_VERSION_ID,
        confirmations=(
            ConfirmationInput(candidate_id=CANDIDATE, disposition=FactDisposition.ABSENT),
        ),
        actor_id=OFFICER,
        expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
    )

    assert result.created is True
    assert result.confirmed_fact_ids == ()
    sql = _sql(connection)
    assert "insert into public.fact_confirmations" in sql
    assert "insert into public.confirmed_facts" not in sql
    assert "insert into public.audit_events" in sql


@pytest.mark.asyncio
async def test_stale_version_fails_closed_without_writing() -> None:
    connection = Connection(
        results=[[(CASE, CASE_VERSION, READY, NOW)]]  # stale_at is set
    )
    repo = _repo(connection)

    with pytest.raises(StaleDocumentVersionError):
        await repo.record_confirmations(
            document_version_id=DOC_VERSION_ID,
            confirmations=(_accepted(),),
            actor_id=OFFICER,
            expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
        )

    assert "insert into public.fact_confirmations" not in _sql(connection)


@pytest.mark.asyncio
async def test_wrong_stage_fails_closed_without_writing() -> None:
    connection = Connection(results=[[(CASE, CASE_VERSION, "EXTRACTED", None)]])
    repo = _repo(connection)

    with pytest.raises(StaleDocumentVersionError):
        await repo.record_confirmations(
            document_version_id=DOC_VERSION_ID,
            confirmations=(_accepted(),),
            actor_id=OFFICER,
            expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
        )

    assert "insert into public.fact_confirmations" not in _sql(connection)


@pytest.mark.asyncio
async def test_missing_version_fails_closed() -> None:
    connection = Connection(results=[[]])  # version lock returns no row
    repo = _repo(connection)

    with pytest.raises(StaleDocumentVersionError):
        await repo.record_confirmations(
            document_version_id=DOC_VERSION_ID,
            confirmations=(_accepted(),),
            actor_id=OFFICER,
            expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
        )


# --- typed evidence edges --------------------------------------------------


def _edge_params(connection: Connection) -> list[tuple[object, ...]]:
    return [
        params
        for query, params in zip(connection.queries, connection.params, strict=True)
        if "insert into public.evidence_edges" in query.lower() and params is not None
    ]


@pytest.mark.asyncio
async def test_confirmation_emits_allowlisted_typed_lineage_edges_idempotently() -> None:
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],  # version lock
            [(CONFIRMATION,)],  # fact_confirmations insert
            [(CONFIRMED_FACT, CANDIDATE, PAGE_REGION, DOC_VERSION_ID)],  # confirmed_facts
            [],  # DERIVED_FROM_CANDIDATE edge
            [],  # LOCATED_IN_REGION edge
            [],  # SOURCED_FROM_DOCUMENT_VERSION edge
            [],  # audit insert
        ]
    )
    repo = _repo(connection)

    await repo.record_confirmations(
        document_version_id=DOC_VERSION_ID,
        confirmations=(_accepted(),),
        actor_id=OFFICER,
        expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
    )

    sql = _sql(connection)
    assert "insert into public.evidence_edges" in sql
    # Idempotent on repeat via the pre-existing unique typed-edge constraint.
    assert "on conflict on constraint evidence_edges_unique_typed_edge" in sql
    assert "do nothing" in sql

    edge_params = _edge_params(connection)
    # Exactly the three allowlisted lineage edges, once each.
    assert len(edge_params) == 3
    triples = {(params[3], params[4], params[6]) for params in edge_params}
    assert triples == {
        ("DERIVED_FROM_CANDIDATE", "CONFIRMED_FACT", "CANDIDATE_FACT"),
        ("LOCATED_IN_REGION", "CONFIRMED_FACT", "PAGE_REGION"),
        ("SOURCED_FROM_DOCUMENT_VERSION", "CONFIRMED_FACT", "DOCUMENT_VERSION"),
    }
    # Every edge is bound to the confirmed fact source and its real FK targets,
    # all sharing the single case_id + case_version (no cross-case edge).
    targets_by_type = {params[6]: params[7] for params in edge_params}
    assert targets_by_type == {
        "CANDIDATE_FACT": CANDIDATE,
        "PAGE_REGION": PAGE_REGION,
        "DOCUMENT_VERSION": DOC_VERSION_ID,
    }
    for params in edge_params:
        assert params[1] == CASE  # case_id
        assert params[2] == CASE_VERSION  # case_version
        assert params[5] == CONFIRMED_FACT  # source_entity_id


@pytest.mark.asyncio
async def test_repeat_confirmation_derives_nothing_and_writes_no_edges() -> None:
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],  # version lock
            [],  # fact_confirmations insert -> conflict
            [(CONFIRMATION,)],  # reselect existing confirmation id
        ]
    )
    repo = _repo(connection)

    await repo.record_confirmations(
        document_version_id=DOC_VERSION_ID,
        confirmations=(_accepted(),),
        actor_id=OFFICER,
        expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
    )

    sql = _sql(connection)
    # A duplicate submission re-derives no fact and therefore writes no edges.
    assert "insert into public.confirmed_facts" not in sql
    assert "insert into public.evidence_edges" not in sql


@pytest.mark.asyncio
async def test_absent_disposition_writes_no_edges() -> None:
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],
            [(CONFIRMATION,)],  # fact_confirmations insert
            [],  # audit insert
        ]
    )
    repo = _repo(connection)

    await repo.record_confirmations(
        document_version_id=DOC_VERSION_ID,
        confirmations=(
            ConfirmationInput(candidate_id=CANDIDATE, disposition=FactDisposition.ABSENT),
        ),
        actor_id=OFFICER,
        expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
    )

    # ABSENT asserts no value, derives no confirmed fact, and so seeds no lineage.
    assert "insert into public.evidence_edges" not in _sql(connection)


# --- reads -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_document_review_reads_current_version_and_candidates() -> None:
    connection = Connection(
        results=[
            [(DOCUMENT, CASE)],  # documents select
            [(DOC_VERSION_ID, CASE_VERSION, 3, READY, "file.pdf")],  # latest version
            [(4,)],  # page count = max(page_number)
            [],  # candidates (none)
        ]
    )
    repo = _repo(connection)

    review = await repo.load_document_review(DOCUMENT)

    assert review is not None
    assert review.case_id == CASE
    assert review.document_version_id == DOC_VERSION_ID
    assert review.document_version == 3
    assert review.stage is DocumentStage.READY_FOR_OFFICER_REVIEW
    assert review.page_count == 4
    assert review.candidates == ()
    sql = _sql(connection)
    assert "from public.documents" in sql
    assert "from public.document_versions" in sql
    assert "from public.page_regions" in sql
    assert "from public.candidate_facts" in sql
    # The current version is the highest version number.
    assert "order by version desc" in sql
    # The candidate read is scoped to the resolved version, case, and case version.
    candidate_index = next(
        i
        for i, q in enumerate(connection.queries)
        if "from public.candidate_facts" in q.lower()
    )
    candidate_params = connection.params[candidate_index]
    assert candidate_params is not None
    assert DOC_VERSION_ID in candidate_params
    assert CASE in candidate_params
    assert CASE_VERSION in candidate_params


@pytest.mark.asyncio
async def test_load_document_review_returns_none_for_missing_document() -> None:
    connection = Connection(results=[[]])  # documents select -> no row
    repo = _repo(connection)

    assert await repo.load_document_review(DOCUMENT) is None


@pytest.mark.asyncio
async def test_load_case_evidence_is_version_scoped() -> None:
    connection = Connection()  # no rows -> empty
    repo = _repo(connection)

    facts = await repo.load_case_evidence(CASE, CASE_VERSION)

    assert facts == ()
    sql = _sql(connection)
    assert "from public.confirmed_facts" in sql
    assert "case_id = %s" in sql
    assert "case_version = %s" in sql
    params = connection.params[0]
    assert params is not None
    assert CASE in params and CASE_VERSION in params


@pytest.mark.asyncio
async def test_load_case_conflicts_is_version_scoped_and_joins_both_sources() -> None:
    connection = Connection()  # no rows -> empty
    repo = _repo(connection)

    conflicts = await repo.load_case_conflicts(CASE, CASE_VERSION)

    assert conflicts == ()
    sql = _sql(connection)
    assert "from public.evidence_conflicts" in sql
    assert "left_confirmed_fact_id" in sql
    assert "right_confirmed_fact_id" in sql
    assert "case_id = %s" in sql
    assert "case_version = %s" in sql
    params = connection.params[0]
    assert params is not None
    assert CASE in params and CASE_VERSION in params
