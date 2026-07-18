"""Contract tests for the Postgres intake-completion persistence adapter.

Mirrors ``tests/contract/postgres/test_document_ingestion_adapter.py``: a fake
connection captures the exact SQL and parameters the adapter issues, proving
the transactional shape and the in-transaction idempotency guard without a live
Postgres.  All identifiers here are synthetic.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creditops.domain.enums import FactDisposition
from creditops.domain.evidence import (
    CandidateFact,
    ConfirmationAuthority,
    ConfirmedFact,
    FactConfirmation,
    PageRegion,
)
from creditops.domain.handoffs import HandoffArtifact
from creditops.infrastructure.postgres.intake import (
    IntakeHandoffSourceMissing,
    PostgresIntakeRepository,
)

CASE = UUID("10000000-0000-0000-0000-000000000004")
SOURCE_TASK = UUID("30000000-0000-0000-0000-000000000004")
EXISTING_HANDOFF = UUID("60000000-0000-0000-0000-000000000004")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CASE_VERSION = 2
NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
GRANTED_AT = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


def _handoff() -> HandoffArtifact:
    candidate = CandidateFact(
        id=uuid4(),
        case_id=CASE,
        case_version=CASE_VERSION,
        document_version_id=uuid4(),
        field_key="requested_amount",
        proposed_value="5000000000",
        confidence=0.9,
        source=PageRegion(page=1, x=0.1, y=0.2, width=0.3, height=0.04),
    )
    confirmation = FactConfirmation(
        id=uuid4(),
        candidate_id=candidate.id,
        disposition=FactDisposition.ACCEPTED,
        authority=ConfirmationAuthority(
            case_id=CASE,
            case_version=CASE_VERSION,
            actor_id=OFFICER,
            assigned_officer_id=OFFICER,
            granted_at=GRANTED_AT,
            source="CASE_ASSIGNMENT",
        ),
        confirmed_at=GRANTED_AT + timedelta(minutes=5),
    )
    fact = ConfirmedFact.from_confirmation(
        id=uuid4(), candidate=candidate, confirmation=confirmation
    )
    return HandoffArtifact(
        id=uuid4(),
        case_id=CASE,
        case_version=CASE_VERSION,
        candidates=(candidate,),
        confirmations=(confirmation,),
        confirmed_facts=(fact,),
    )


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


def _repo(connection: Connection) -> PostgresIntakeRepository:
    return PostgresIntakeRepository(lambda: ConnectionContext(connection))


def _sql(connection: Connection) -> str:
    return " ".join(connection.queries).lower()


@pytest.mark.asyncio
async def test_persist_handoff_is_transactional_and_hits_public_handoffs() -> None:
    handoff = _handoff()
    # guard select -> no existing; source task select -> one ingestion task.
    connection = Connection(results=[[], [(SOURCE_TASK,)]])
    repo = _repo(connection)

    persisted = await repo.persist_handoff(handoff, actor_id=OFFICER)

    assert persisted.created is True
    assert persisted.handoff_id == handoff.id
    sql = _sql(connection)
    assert "insert into public.handoffs" in sql
    # The idempotency guard selects an existing current handoff first.
    assert "select id from public.handoffs" in sql
    assert "state = %s" in sql
    # Provenance source is the ingestion task, resolved before the insert; the
    # task type is bound as a parameter, never interpolated into the SQL.
    assert "from public.processing_tasks" in sql
    source_index = next(
        i for i, q in enumerate(connection.queries) if "processing_tasks" in q.lower()
    )
    source_params = connection.params[source_index]
    assert source_params is not None
    assert "DOCUMENT_INGESTION" in source_params
    # The whole persist runs inside one transaction.
    assert connection.transactions_opened == 1
    assert all(connection.executed_in_transaction)
    # The insert binds identifiers as parameters, never interpolated.
    insert_index = next(
        i for i, q in enumerate(connection.queries) if "insert into public.handoffs" in q.lower()
    )
    insert_params = connection.params[insert_index]
    assert insert_params is not None
    assert handoff.id in insert_params
    assert CASE in insert_params
    assert CASE_VERSION in insert_params
    assert SOURCE_TASK in insert_params
    assert OFFICER in insert_params


@pytest.mark.asyncio
async def test_persist_handoff_is_idempotent_on_existing_current_handoff() -> None:
    handoff = _handoff()
    # guard select returns an existing current handoff id.
    connection = Connection(results=[[(EXISTING_HANDOFF,)]])
    repo = _repo(connection)

    persisted = await repo.persist_handoff(handoff, actor_id=OFFICER)

    assert persisted.created is False
    assert persisted.handoff_id == EXISTING_HANDOFF
    sql = _sql(connection)
    assert "insert into public.handoffs" not in sql
    # The guard runs inside the transaction.
    assert connection.transactions_opened == 1


@pytest.mark.asyncio
async def test_persist_handoff_fails_closed_without_a_source_task() -> None:
    handoff = _handoff()
    # guard select -> no existing; source task select -> none.
    connection = Connection(results=[[], []])
    repo = _repo(connection)

    with pytest.raises(IntakeHandoffSourceMissing):
        await repo.persist_handoff(handoff, actor_id=OFFICER)

    assert "insert into public.handoffs" not in _sql(connection)


@pytest.mark.asyncio
async def test_load_intake_evidence_scopes_every_read_by_case_and_version() -> None:
    connection = Connection()  # every read returns no rows -> an empty view
    repo = _repo(connection)

    view = await repo.load_intake_evidence(CASE, CASE_VERSION)

    assert view.case_id == CASE
    assert view.case_version == CASE_VERSION
    assert view.candidates == ()
    assert view.confirmed_facts == ()
    sql = _sql(connection)
    assert "from public.candidate_facts" in sql
    assert "from public.fact_confirmations" in sql
    assert "from public.confirmed_facts" in sql
    assert "from public.evidence_conflicts" in sql
    assert "from public.evidence_gaps" in sql
    # Each read is scoped by both case_id and case_version.
    for query, params in zip(connection.queries, connection.params, strict=True):
        assert "case_id = %s" in query.lower()
        assert "case_version = %s" in query.lower()
        assert params is not None
        assert CASE in params
        assert CASE_VERSION in params


@pytest.mark.asyncio
async def test_load_current_handoff_scopes_and_reads_ready_state() -> None:
    connection = Connection(
        results=[[(EXISTING_HANDOFF, "READY_FOR_SPECIALIST_REVIEW", CASE_VERSION, NOW)]]
    )
    repo = _repo(connection)

    current = await repo.load_current_handoff(CASE, CASE_VERSION)

    assert current is not None
    assert current.id == EXISTING_HANDOFF
    assert current.state == "READY_FOR_SPECIALIST_REVIEW"
    assert current.case_version == CASE_VERSION
    sql = _sql(connection)
    assert "from public.handoffs" in sql
    params = connection.params[0]
    assert params is not None
    assert CASE in params and CASE_VERSION in params


@pytest.mark.asyncio
async def test_has_current_handoff_returns_the_exists_flag() -> None:
    connection = Connection(results=[[(True,)]])
    repo = _repo(connection)

    assert await repo.has_current_handoff(CASE, CASE_VERSION) is True
    sql = _sql(connection)
    assert "exists" in sql
    assert "from public.handoffs" in sql


@pytest.mark.asyncio
async def test_append_audit_writes_a_human_actor_event() -> None:
    from creditops.application.ports.intake import IntakeAuditEvent

    connection = Connection()
    repo = _repo(connection)

    await repo.append_audit(
        IntakeAuditEvent(
            case_id=CASE,
            case_version=CASE_VERSION,
            event_type="INTAKE_HANDOFF_CREATED",
            actor_id=OFFICER,
            artifact_type="HANDOFF",
            artifact_id=EXISTING_HANDOFF,
        )
    )

    sql = _sql(connection)
    assert "insert into public.audit_events" in sql
    assert connection.transactions_opened == 1
    params = connection.params[0]
    assert params is not None
    assert OFFICER in params
    assert "HUMAN:INTAKE_OFFICER" in params
