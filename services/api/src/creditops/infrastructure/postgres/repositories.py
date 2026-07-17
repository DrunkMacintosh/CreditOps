from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.repositories import (
    AuditEvent,
    CaseRecord,
    ForbiddenError,
)
from creditops.application.unit_of_work import ActorContext


class DatabaseCursor(Protocol):
    async def fetchone(self) -> Sequence[Any] | None: ...

    async def fetchall(self) -> list[Sequence[Any]]: ...


class DatabaseConnection(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[None]: ...

    async def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> DatabaseCursor: ...


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[DatabaseConnection]: ...


_CASE_COLUMNS = """
    cc.id,
    cc.case_version,
    assignment.officer_id,
    financing_request.requested_amount::text,
    financing_request.purpose_vi,
    cc.created_at
"""

_CASE_FROM = """
    from public.credit_cases as cc
    join public.case_assignments as assignment
      on assignment.case_id = cc.id
     and assignment.revoked_at is null
    join lateral (
      select requested_amount, purpose_vi
      from public.financing_requests
      where case_id = cc.id
        and case_version = cc.case_version
      order by request_version desc
      limit 1
    ) as financing_request on true
"""


def _case_from_row(row: Sequence[Any]) -> CaseRecord:
    requested_amount = row[3]
    purpose_vi = row[4]
    if not isinstance(requested_amount, str) or not isinstance(purpose_vi, str):
        raise RuntimeError("Current financing request data is missing")
    return CaseRecord(
        id=cast(UUID, row[0]),
        version=cast(int, row[1]),
        assigned_officer_id=cast(UUID, row[2]),
        requested_amount=requested_amount,
        purpose_vi=purpose_vi,
        created_at=cast(datetime, row[5]),
    )


class PostgresCaseRepository:
    def __init__(self, connection: DatabaseConnection) -> None:
        self._connection = connection
        self._created_in_transaction: dict[UUID, CaseRecord] = {}

    async def create(
        self,
        *,
        actor_id: UUID,
        assigned_officer_id: UUID,
        requested_amount: str,
        purpose_vi: str,
    ) -> CaseRecord:
        cursor = await self._connection.execute(
            """
            insert into public.credit_cases (workflow_state, created_by)
            values (%s, %s)
            returning id, case_version, created_at
            """,
            ("INTAKE_DRAFT", actor_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Case insert did not return a record")

        case_id = cast(UUID, row[0])
        await self._connection.execute(
            """
            insert into public.case_assignments (
              case_id, officer_id, assigned_by
            ) values (%s, %s, %s)
            """,
            (case_id, assigned_officer_id, actor_id),
        )
        await self._connection.execute(
            """
            insert into public.financing_requests (
              case_id,
              case_version,
              request_version,
              requested_amount,
              purpose_vi,
              created_by
            ) values (%s, %s, %s, %s, %s, %s)
            """,
            (
                case_id,
                cast(int, row[1]),
                1,
                requested_amount,
                purpose_vi,
                actor_id,
            ),
        )
        record = CaseRecord(
            id=case_id,
            version=cast(int, row[1]),
            assigned_officer_id=assigned_officer_id,
            requested_amount=requested_amount,
            purpose_vi=purpose_vi,
            created_at=cast(datetime, row[2]),
        )
        self._created_in_transaction[case_id] = record
        return record

    async def require_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord:
        pending = self._created_in_transaction.get(case_id)
        if pending is not None:
            cursor = await self._connection.execute(
                """
                select 1
                from public.case_assignments
                where case_id = %s
                  and officer_id = %s
                  and revoked_at is null
                """,
                (case_id, actor_id),
            )
            if await cursor.fetchone() is None:
                raise ForbiddenError
            return pending

        record = await self.get_assigned(case_id, actor_id)
        if record is None:
            raise ForbiddenError
        return record

    async def get_assigned(self, case_id: UUID, actor_id: UUID) -> CaseRecord | None:
        cursor = await self._connection.execute(
            f"""
            select {_CASE_COLUMNS}
            {_CASE_FROM}
            where cc.id = %s
              and assignment.officer_id = %s
            """,
            (case_id, actor_id),
        )
        row = await cursor.fetchone()
        return None if row is None else _case_from_row(row)

    async def list_assigned(
        self,
        actor_id: UUID,
        *,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[CaseRecord], UUID | None]:
        cursor_position: tuple[datetime, UUID] | None = None
        if cursor is not None:
            cursor_result = await self._connection.execute(
                """
                select cc.created_at, cc.id
                from public.credit_cases as cc
                join public.case_assignments as assignment
                  on assignment.case_id = cc.id
                 and assignment.revoked_at is null
                where cc.id = %s
                  and assignment.officer_id = %s
                """,
                (cursor, actor_id),
            )
            cursor_row = await cursor_result.fetchone()
            if cursor_row is None:
                return [], None
            cursor_position = (cast(datetime, cursor_row[0]), cast(UUID, cursor_row[1]))

        position_clause = ""
        params: list[object] = [actor_id]
        if cursor_position is not None:
            position_clause = "and (cc.created_at, cc.id) < (%s, %s)"
            params.extend(cursor_position)
        params.append(limit + 1)

        result = await self._connection.execute(
            f"""
            select {_CASE_COLUMNS}
            {_CASE_FROM}
            where assignment.officer_id = %s
              {position_clause}
            order by cc.created_at desc, cc.id desc
            limit %s
            """,
            params,
        )
        records = [_case_from_row(row) for row in await result.fetchall()]
        page = records[:limit]
        next_cursor = page[-1].id if len(records) > limit else None
        return page, next_cursor


class PostgresAuditRepository:
    def __init__(self, connection: DatabaseConnection) -> None:
        self._connection = connection

    async def append(self, event: AuditEvent) -> None:
        event_data = dict(event.event_data)
        event_data["correlationId"] = event.request_id
        await self._connection.execute(
            """
            insert into public.audit_events (
              case_id,
              case_version,
              event_type,
              actor_type,
              actor_id,
              artifact_type,
              artifact_id,
              event_data
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.case_id,
                event.case_version,
                event.event_type,
                "HUMAN",
                event.actor_id,
                event.artifact_type,
                event.artifact_id,
                Jsonb(event_data),
            ),
        )


class PostgresUnitOfWork:
    def __init__(self, connection_factory: ConnectionFactory, actor: ActorContext) -> None:
        self._connection_factory = connection_factory
        self._actor = actor
        self._connection_context: AbstractAsyncContextManager[DatabaseConnection] | None = None
        self._transaction_context: AbstractAsyncContextManager[None] | None = None
        self.cases: PostgresCaseRepository
        self.audit: PostgresAuditRepository

    async def __aenter__(self) -> Self:
        self._connection_context = self._connection_factory()
        connection = await self._connection_context.__aenter__()
        self._transaction_context = connection.transaction()
        transaction_entered = False
        try:
            await self._transaction_context.__aenter__()
            transaction_entered = True

            claims = json.dumps(
                {
                    "sub": str(self._actor.actor_id),
                    "roles": sorted(self._actor.roles),
                },
                separators=(",", ":"),
            )
            context_values = (
                ("request.jwt.claim.sub", str(self._actor.actor_id)),
                ("request.jwt.claims", claims),
                ("application.actor_id", str(self._actor.actor_id)),
                ("application.request_id", self._actor.request_id),
            )
            for name, value in context_values:
                await connection.execute(
                    f"select set_config('{name}', %s, true)",
                    (value,),
                )
        except BaseException as exc:
            try:
                if transaction_entered:
                    await self._transaction_context.__aexit__(
                        type(exc),
                        exc,
                        exc.__traceback__,
                    )
            finally:
                await self._connection_context.__aexit__(
                    type(exc),
                    exc,
                    exc.__traceback__,
                )
            raise

        self.cases = PostgresCaseRepository(connection)
        self.audit = PostgresAuditRepository(connection)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._transaction_context is not None:
                await self._transaction_context.__aexit__(exc_type, exc_value, traceback)
        finally:
            if self._connection_context is not None:
                await self._connection_context.__aexit__(exc_type, exc_value, traceback)


class PostgresUnitOfWorkFactory:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def __call__(self, actor: ActorContext) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self._connection_factory, actor)
