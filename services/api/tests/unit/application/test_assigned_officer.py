from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

from creditops.api.auth import ActorContext
from creditops.application.ports.repositories import (
    AuditEvent,
    CaseRecord,
    ForbiddenError,
)
from creditops.application.use_cases.create_case import CreateCase, CreateCaseCommand
from creditops.infrastructure.postgres.repositories import (
    PostgresCaseRepository,
    PostgresUnitOfWork,
)

OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OFFICER_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class FakeCaseRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, CaseRecord] = {}
        self.require_assigned_calls: list[tuple[UUID, UUID]] = []

    async def create(
        self,
        *,
        actor_id: UUID,
        assigned_officer_id: UUID,
        requested_amount: str,
        purpose_vi: str,
    ) -> CaseRecord:
        record = CaseRecord(
            id=uuid4(),
            version=1,
            assigned_officer_id=assigned_officer_id,
            requested_amount=requested_amount,
            purpose_vi=purpose_vi,
            created_at=datetime.now(UTC),
        )
        self.records[record.id] = record
        return record

    async def require_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord:
        self.require_assigned_calls.append((case_id, actor_id))
        record = self.records.get(case_id)
        if record is None or record.assigned_officer_id != actor_id:
            raise ForbiddenError
        return record

    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        try:
            return await self.require_assigned(case_id, actor_id)
        except ForbiddenError:
            return None

    async def list_assigned(
        self, actor_id: UUID, *, cursor: UUID | None, limit: int
    ) -> tuple[list[CaseRecord], UUID | None]:
        del cursor
        records = [
            record for record in self.records.values() if record.assigned_officer_id == actor_id
        ]
        return records[:limit], None


class FakeAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.cases = FakeCaseRepository()
        self.audit = FakeAuditRepository()
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeUnitOfWork:
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.exited = True


@pytest.mark.asyncio
async def test_other_officer_cannot_access_case() -> None:
    repository = FakeCaseRepository()
    case = await repository.create(
        actor_id=OFFICER_A,
        assigned_officer_id=OFFICER_A,
        requested_amount="5000000000",
        purpose_vi="Bổ sung vốn lưu động",
    )

    with pytest.raises(ForbiddenError):
        await repository.require_assigned(case.id, OFFICER_B)


@pytest.mark.asyncio
async def test_create_case_rechecks_assignment_and_appends_audit() -> None:
    uow = FakeUnitOfWork()
    actor = ActorContext(
        actor_id=OFFICER_A,
        roles=frozenset({"INTAKE_OFFICER"}),
        request_id="request-123",
    )

    result = await CreateCase(lambda _: uow).execute(
        actor,
        CreateCaseCommand(
            requested_amount="5000000000",
            purpose_vi="Bổ sung vốn lưu động",
        ),
    )

    assert uow.entered is True
    assert uow.exited is True
    assert uow.cases.require_assigned_calls == [(result.id, OFFICER_A)]
    assert [event.event_type for event in uow.audit.events] == ["CASE_CREATED"]
    assert uow.audit.events[0].request_id == "request-123"


class FakeTransaction(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class FakeConnection:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.executions.append((query, params))


class FakeConnectionContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


@pytest.mark.asyncio
async def test_postgres_uow_sets_transaction_local_context_with_parameters() -> None:
    connection = FakeConnection()
    actor = ActorContext(
        actor_id=OFFICER_A,
        roles=frozenset({"INTAKE_OFFICER"}),
        request_id="request-123",
    )
    uow = PostgresUnitOfWork(lambda: FakeConnectionContext(connection), actor)

    async with uow:
        pass

    context_statements = [item for item in connection.executions if "set_config" in item[0]]
    assert len(context_statements) == 4
    assert all("%s" in query for query, _ in context_statements)
    assert all(params is not None for _, params in context_statements)
    assert all(str(OFFICER_A) not in query for query, _ in context_statements)
    assert all("request-123" not in query for query, _ in context_statements)


class ScriptedCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class ScriptedConnection:
    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self.results = results
        self.executions: list[tuple[str, tuple[Any, ...] | list[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: tuple[Any, ...] | list[object] | None = None,
    ) -> ScriptedCursor:
        self.executions.append((query, params))
        return ScriptedCursor(self.results.pop(0) if self.results else [])


@pytest.mark.asyncio
async def test_postgres_create_persists_structured_financing_request() -> None:
    case_id = uuid4()
    connection = ScriptedConnection([[(case_id, 1, datetime.now(UTC))], [], []])
    repository = PostgresCaseRepository(connection)

    await repository.create(
        actor_id=OFFICER_A,
        assigned_officer_id=OFFICER_A,
        requested_amount="5000000000",
        purpose_vi="Bổ sung vốn lưu động",
    )

    sql = "\n".join(query for query, _ in connection.executions)
    assert "insert into public.financing_requests" in sql


@pytest.mark.asyncio
async def test_postgres_case_reads_use_financing_request_not_audit_history() -> None:
    case_id = uuid4()
    row = (
        case_id,
        1,
        OFFICER_A,
        "5000000000",
        "Bổ sung vốn lưu động",
        datetime.now(UTC),
    )
    connection = ScriptedConnection([[row]])
    repository = PostgresCaseRepository(connection)

    result = await repository.get_assigned(case_id, OFFICER_A)

    assert result is not None
    sql = connection.executions[0][0]
    assert "public.financing_requests" in sql
    assert "public.audit_events" not in sql


class TrackingTransaction(AbstractAsyncContextManager[None]):
    def __init__(self) -> None:
        self.exited = False

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.exited = True


class FailingContextConnection:
    def __init__(self) -> None:
        self.transaction_context = TrackingTransaction()

    def transaction(self) -> TrackingTransaction:
        return self.transaction_context

    async def execute(
        self,
        query: str,
        params: tuple[Any, ...] | None = None,
    ) -> None:
        del query, params
        raise RuntimeError("context setup failed")


class TrackingConnectionContext(AbstractAsyncContextManager[FailingContextConnection]):
    def __init__(self, connection: FailingContextConnection) -> None:
        self.connection = connection
        self.exited = False

    async def __aenter__(self) -> FailingContextConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.exited = True


@pytest.mark.asyncio
async def test_postgres_uow_cleans_up_when_context_setup_fails() -> None:
    connection = FailingContextConnection()
    connection_context = TrackingConnectionContext(connection)
    actor = ActorContext(
        actor_id=OFFICER_A,
        roles=frozenset({"INTAKE_OFFICER"}),
        request_id="request-123",
    )
    uow = PostgresUnitOfWork(lambda: connection_context, actor)

    with pytest.raises(RuntimeError, match="context setup failed"):
        await uow.__aenter__()

    assert connection.transaction_context.exited is True
    assert connection_context.exited is True
