"""P0-B INDEPENDENT AUDIT -- live typed evidence-graph edge materialization.

These are NEW, standalone audit tests (they do not import or edit the concurrent
build task's ``test_evidence_review_adapter.py``).  They re-assert, against the
CURRENT adapter source, that the ONE production path which materializes typed
``evidence_edges`` today -- ``PostgresEvidenceReviewRepository.record_confirmations``
deriving a confirmed fact -- emits exactly the allowlisted lineage edges, binds
them to the DB-derived FK targets (not caller/model input), writes them inside
the single confirmation transaction, is idempotent on repeat, and never mutates
or deletes an existing edge.

A local fake connection records the exact SQL + params (there is no live
Postgres here -- see NOT_TESTABLE notes in the audit report for the real
constraint / RLS / atomic-rollback behaviours this cannot prove).  Every
identifier is synthetic.
"""

from __future__ import annotations

import dataclasses
from contextlib import AbstractAsyncContextManager
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

CASE = UUID("10000000-0000-0000-0000-0000000000b1")
CASE_VERSION = 4
DOC_VERSION_ID = UUID("21000000-0000-0000-0000-0000000000b1")
# The candidate the officer *submits* in the request payload.
INPUT_CANDIDATE = UUID("22000000-0000-0000-0000-0000000000b1")
CONFIRMATION = UUID("23000000-0000-0000-0000-0000000000b1")
# The identities the DB RETURNING (post-trigger) hands back.  Deliberately made
# DISTINCT sentinels so a test can prove the edges bind the DB-derived targets,
# not any caller-supplied value.
DERIVED_FACT = UUID("24000000-0000-0000-0000-0000000000b1")
DERIVED_CANDIDATE = UUID("2200dead-0000-0000-0000-0000000000b1")
DERIVED_REGION = UUID("25000000-0000-0000-0000-0000000000b1")
DERIVED_DOC_VERSION = UUID("2100beef-0000-0000-0000-0000000000b1")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
READY = "READY_FOR_OFFICER_REVIEW"


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


def _edge_params(connection: Connection) -> list[tuple[object, ...]]:
    return [
        params
        for query, params in zip(connection.queries, connection.params, strict=True)
        if "insert into public.evidence_edges" in query.lower() and params is not None
    ]


def _derives_edges_connection() -> Connection:
    """Result sequence for one ACCEPTED confirmation that derives a fact.

    version lock -> fact_confirmations returning id -> confirmed_facts returning
    (id, candidate, region, doc_version) -> 3 edge inserts -> audit insert.
    """

    return Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],
            [(CONFIRMATION,)],
            [(DERIVED_FACT, DERIVED_CANDIDATE, DERIVED_REGION, DERIVED_DOC_VERSION)],
            [],  # DERIVED_FROM_CANDIDATE
            [],  # LOCATED_IN_REGION
            [],  # SOURCED_FROM_DOCUMENT_VERSION
            [],  # audit
        ]
    )


def _accepted() -> ConfirmationInput:
    return ConfirmationInput(
        candidate_id=INPUT_CANDIDATE, disposition=FactDisposition.ACCEPTED
    )


async def _record(connection: Connection, confirmation: ConfirmationInput) -> object:
    return await _repo(connection).record_confirmations(
        document_version_id=DOC_VERSION_ID,
        confirmations=(confirmation,),
        actor_id=OFFICER,
        expected_document_stage=DocumentStage.READY_FOR_OFFICER_REVIEW,
    )


# --- (1) exactly the required typed edges ----------------------------------


@pytest.mark.asyncio
async def test_derivation_emits_exactly_three_allowlisted_typed_edges() -> None:
    connection = _derives_edges_connection()

    await _record(connection, _accepted())

    edges = _edge_params(connection)
    assert len(edges) == 3
    # (edge_type, source_entity_type, target_entity_type)
    triples = {(p[3], p[4], p[6]) for p in edges}
    assert triples == {
        ("DERIVED_FROM_CANDIDATE", "CONFIRMED_FACT", "CANDIDATE_FACT"),
        ("LOCATED_IN_REGION", "CONFIRMED_FACT", "PAGE_REGION"),
        ("SOURCED_FROM_DOCUMENT_VERSION", "CONFIRMED_FACT", "DOCUMENT_VERSION"),
    }


# --- (2) provenance binds the correct case/version + real targets ----------


@pytest.mark.asyncio
async def test_every_edge_is_case_version_scoped_and_sourced_at_the_confirmed_fact() -> None:
    connection = _derives_edges_connection()

    await _record(connection, _accepted())

    for p in _edge_params(connection):
        assert p[1] == CASE  # case_id (from the locked version row)
        assert p[2] == CASE_VERSION  # case_version
        assert p[5] == DERIVED_FACT  # source_entity_id == the confirmed fact
    targets_by_type = {p[6]: p[7] for p in _edge_params(connection)}
    assert targets_by_type == {
        "CANDIDATE_FACT": DERIVED_CANDIDATE,
        "PAGE_REGION": DERIVED_REGION,
        "DOCUMENT_VERSION": DERIVED_DOC_VERSION,
    }


# --- (12) model / caller payload cannot inject an authoritative edge --------


@pytest.mark.asyncio
async def test_edge_targets_come_from_db_returning_not_caller_input() -> None:
    # The submitted candidate id differs from every DB-derived identity.  If the
    # writer echoed caller input, the DERIVED_FROM_CANDIDATE edge would point at
    # INPUT_CANDIDATE.  It must instead point at the RETURNING-derived candidate.
    connection = _derives_edges_connection()

    await _record(connection, _accepted())

    edges = _edge_params(connection)
    all_target_ids = {p[7] for p in edges}
    assert INPUT_CANDIDATE not in all_target_ids
    assert DERIVED_CANDIDATE in all_target_ids
    # Nothing on the ConfirmationInput surface can name an edge type / endpoint.
    assert {f.name for f in dataclasses.fields(ConfirmationInput)} == {
        "candidate_id",
        "disposition",
        "corrected_value",
        "rationale",
    }


# --- (10) edges are written inside the single confirmation transaction ------


@pytest.mark.asyncio
async def test_all_edge_writes_share_the_one_confirmation_transaction() -> None:
    connection = _derives_edges_connection()

    await _record(connection, _accepted())

    assert connection.transactions_opened == 1
    # Every statement -- version lock, confirmation, confirmed fact, edges,
    # audit -- executed at transaction depth > 0 (no edge-only / artifact-only
    # write can escape the confirmation's atomic boundary).
    assert all(connection.executed_in_transaction)
    edge_indexes = [
        i
        for i, q in enumerate(connection.queries)
        if "insert into public.evidence_edges" in q.lower()
    ]
    assert edge_indexes
    assert all(connection.executed_in_transaction[i] for i in edge_indexes)


# --- (3) idempotent persistence, stable identity ---------------------------


@pytest.mark.asyncio
async def test_edges_persist_via_unique_typed_edge_on_conflict_do_nothing() -> None:
    connection = _derives_edges_connection()

    await _record(connection, _accepted())

    edge_query = next(
        q for q in connection.queries if "insert into public.evidence_edges" in q.lower()
    ).lower()
    assert "on conflict on constraint evidence_edges_unique_typed_edge" in edge_query
    assert "do nothing" in edge_query


@pytest.mark.asyncio
async def test_repeat_confirmation_conflict_derives_and_writes_no_edges() -> None:
    # fact_confirmations insert hits the one-disposition conflict -> reselect ->
    # created False -> no confirmed fact -> no edges.
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],
            [],  # confirmation insert conflict
            [(CONFIRMATION,)],  # reselect existing
        ]
    )

    result = await _record(connection, _accepted())

    assert result.created is False
    sql = _sql(connection)
    assert "insert into public.confirmed_facts" not in sql
    assert "insert into public.evidence_edges" not in sql


@pytest.mark.parametrize(
    "disposition", [FactDisposition.ABSENT, FactDisposition.UNREADABLE]
)
@pytest.mark.asyncio
async def test_value_absent_dispositions_write_no_edges(
    disposition: FactDisposition,
) -> None:
    connection = Connection(
        results=[
            [(CASE, CASE_VERSION, READY, None)],
            [(CONFIRMATION,)],  # confirmation insert
            [],  # audit
        ]
    )

    await _record(
        connection, ConfirmationInput(candidate_id=INPUT_CANDIDATE, disposition=disposition)
    )

    assert "insert into public.evidence_edges" not in _sql(connection)


# --- (9) superseded history intact: the writer never updates/deletes edges --


@pytest.mark.asyncio
async def test_confirmation_never_updates_or_deletes_an_existing_edge() -> None:
    connection = _derives_edges_connection()

    await _record(connection, _accepted())

    sql = _sql(connection)
    assert "update public.evidence_edges" not in sql
    assert "delete from public.evidence_edges" not in sql


# --- (4)/(10) fail-closed: a stale version writes nothing at all -----------


@pytest.mark.asyncio
async def test_stale_version_writes_no_confirmation_and_no_edge() -> None:
    connection = Connection(results=[[(CASE, CASE_VERSION, READY, object())]])

    with pytest.raises(StaleDocumentVersionError):
        await _record(connection, _accepted())

    sql = _sql(connection)
    assert "insert into public.fact_confirmations" not in sql
    assert "insert into public.evidence_edges" not in sql
