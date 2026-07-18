from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self, cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from creditops.application.ports.repositories import (
    AuditEvent,
    CaseRecord,
    ForbiddenError,
    IdempotencyRecord,
    UploadIntentRecord,
    UploadRegistration,
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
    def __init__(
        self,
        connection: DatabaseConnection,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._connection = connection
        self._id_factory = id_factory
        self._created_in_transaction: dict[UUID, CaseRecord] = {}

    async def create(
        self,
        *,
        actor_id: UUID,
        assigned_officer_id: UUID,
        requested_amount: str,
        purpose_vi: str,
    ) -> CaseRecord:
        case_id = self._id_factory()
        await self._connection.execute(
            """
            insert into public.credit_cases (
              id, case_version, workflow_state, created_by
            ) values (%s, %s, %s, %s)
            """,
            (case_id, 1, "INTAKE_DRAFT", actor_id),
        )
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
                1,
                1,
                requested_amount,
                purpose_vi,
                actor_id,
            ),
        )
        record = await self.get_assigned(case_id, assigned_officer_id)
        if record is None:
            raise RuntimeError("Created case is not visible to its active assigned officer")
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

    async def list_assignment_roles(self, case_id: UUID, actor_id: UUID) -> frozenset[str]:
        cursor = await self._connection.execute(
            """
            select case_role
            from public.case_assignments
            where case_id = %s
              and officer_id = %s
              and revoked_at is null
            """,
            (case_id, actor_id),
        )
        rows = await cursor.fetchall()
        return frozenset(cast(str, row[0]) for row in rows)


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


class PostgresUploadRepository:
    def __init__(
        self,
        connection: DatabaseConnection,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._connection = connection
        self._id_factory = id_factory

    @staticmethod
    def _intent_from_row(row: Sequence[Any]) -> UploadIntentRecord:
        return UploadIntentRecord(
            id=cast(UUID, row[0]),
            case_id=cast(UUID, row[1]),
            case_version=cast(int, row[2]),
            assigned_officer_id=cast(UUID, row[3]),
            bucket_id=cast(str, row[4]),
            object_key=cast(str, row[5]),
            original_filename=cast(str, row[6]),
            accepted_content_type=cast(str, row[7]),
            declared_size_bytes=cast(int, row[8]),
            expires_at=cast(datetime, row[9]),
            consumed_at=cast(datetime | None, row[10]),
        )

    async def create_intent(
        self,
        *,
        intent_id: UUID,
        case: CaseRecord,
        original_filename: str,
        content_type: str,
        declared_size_bytes: int,
        bucket_id: str,
        object_key: str,
        expires_at: datetime,
    ) -> UploadIntentRecord:
        cursor = await self._connection.execute(
            """
            insert into public.upload_intents (
              id, case_id, case_version, assigned_officer_id, bucket_id,
              object_key, original_filename, accepted_content_type,
              size_ceiling, declared_size_bytes, expires_at, status
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
            returning id, case_id, case_version, assigned_officer_id,
                      bucket_id, object_key, original_filename,
                      accepted_content_type, declared_size_bytes,
                      expires_at, consumed_at
            """,
            (
                intent_id,
                case.id,
                case.version,
                case.assigned_officer_id,
                bucket_id,
                object_key,
                original_filename,
                content_type,
                declared_size_bytes,
                declared_size_bytes,
                expires_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Upload intent insert returned no row")
        return self._intent_from_row(row)

    async def get_intent(self, intent_id: UUID, actor_id: UUID) -> UploadIntentRecord | None:
        cursor = await self._connection.execute(
            """
            select intent.id, intent.case_id, intent.case_version,
                   intent.assigned_officer_id, intent.bucket_id,
                   intent.object_key, intent.original_filename,
                   intent.accepted_content_type, intent.declared_size_bytes,
                   intent.expires_at, intent.consumed_at
            from public.upload_intents as intent
            join public.case_assignments as assignment
              on assignment.case_id = intent.case_id
             and assignment.officer_id = intent.assigned_officer_id
             and assignment.revoked_at is null
            where intent.id = %s and intent.assigned_officer_id = %s
            for update
            """,
            (intent_id, actor_id),
        )
        row = await cursor.fetchone()
        return None if row is None else self._intent_from_row(row)

    async def find_idempotency(
        self,
        *,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        cursor = await self._connection.execute(
            """
            select id, case_id, actor_id, operation, idempotency_key, request_sha256,
                   lease_owner, lease_until, completed_at, response_status, response_data
            from public.idempotency_records
            where actor_id = %s and operation = %s and idempotency_key = %s
            for update
            """,
            (actor_id, operation, idempotency_key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        response_data = row[10]
        return IdempotencyRecord(
            id=cast(UUID, row[0]),
            case_id=cast(UUID, row[1]),
            actor_id=cast(UUID, row[2]),
            operation=cast(str, row[3]),
            idempotency_key=cast(str, row[4]),
            request_sha256=cast(str, row[5]),
            lease_owner=cast(UUID | None, row[6]),
            lease_until=cast(datetime | None, row[7]),
            completed_at=cast(datetime | None, row[8]),
            response_status=cast(int | None, row[9]),
            response_data=(
                cast(dict[str, object], response_data) if isinstance(response_data, dict) else None
            ),
        )

    async def reserve_idempotency(
        self,
        *,
        case_id: UUID,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
        request_sha256: str,
        lease_until: datetime,
        lease_owner: UUID,
    ) -> IdempotencyRecord:
        await self._connection.execute(
            """
            insert into public.idempotency_records (
              case_id, actor_id, operation, idempotency_key,
              request_sha256, lease_owner, lease_until
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (actor_id, operation, idempotency_key) do update
              set lease_owner = excluded.lease_owner,
                  lease_until = excluded.lease_until
            where idempotency_records.completed_at is null
              and (
                idempotency_records.lease_until is null
                or idempotency_records.lease_until <= statement_timestamp()
              )
            """,
            (
                case_id,
                actor_id,
                operation,
                idempotency_key,
                request_sha256,
                lease_owner,
                lease_until,
            ),
        )
        record = await self.find_idempotency(
            actor_id=actor_id,
            operation=operation,
            idempotency_key=idempotency_key,
        )
        if record is None:
            raise RuntimeError("idempotency reservation disappeared")
        return record

    async def complete_idempotency(
        self,
        *,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
        response_status: int,
        response_data: Mapping[str, object],
        lease_owner: UUID,
    ) -> None:
        await self._connection.execute(
            """
            update public.idempotency_records
            set response_status = %s,
                response_schema_version = '1',
                response_data = %s,
                completed_at = clock_timestamp(),
                lease_owner = null,
                lease_until = null
            where actor_id = %s and operation = %s and idempotency_key = %s
              and lease_owner = %s
              and lease_until > statement_timestamp()
              and completed_at is null
            """,
            (
                response_status,
                Jsonb(dict(response_data)),
                actor_id,
                operation,
                idempotency_key,
                lease_owner,
            ),
        )

    async def find_duplicate_document(
        self,
        *,
        case_id: UUID,
        content_sha256: str,
    ) -> UUID | None:
        cursor = await self._connection.execute(
            """
            select document_id
            from public.document_versions
            where case_id = %s and content_sha256 = %s and stale_at is null
            order by created_at asc
            limit 1
            """,
            (case_id, content_sha256),
        )
        row = await cursor.fetchone()
        return None if row is None else cast(UUID, row[0])

    async def register_verified_upload(
        self,
        *,
        intent: UploadIntentRecord,
        immutable_bucket: str,
        immutable_key: str,
        detected_content_type: str,
        content_sha256: str,
        task_input: Mapping[str, object],
        actor_id: UUID,
    ) -> UploadRegistration:
        document_id = self._id_factory()
        document_version_id = self._id_factory()
        task_id = self._id_factory()
        await self._connection.execute(
            "insert into public.documents (id, case_id, created_by) values (%s, %s, %s)",
            (document_id, intent.case_id, actor_id),
        )
        await self._connection.execute(
            """
            insert into public.document_versions (
              id, document_id, case_id, case_version, version, stage,
              storage_bucket, storage_object_key, original_filename,
              declared_content_type, detected_content_type, byte_size,
              content_sha256, created_by
            ) values (%s, %s, %s, %s, 1, 'REGISTERED', %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                document_version_id,
                document_id,
                intent.case_id,
                intent.case_version,
                immutable_bucket,
                immutable_key,
                intent.original_filename,
                intent.accepted_content_type,
                detected_content_type,
                intent.declared_size_bytes,
                content_sha256,
                actor_id,
            ),
        )
        await self._connection.execute(
            """
            insert into public.processing_tasks (
              id, case_id, case_version, document_version_id, task_type,
              status, max_attempts, input_schema_version, input_payload,
              idempotency_key
            ) values (%s, %s, %s, %s, 'DOCUMENT_INGESTION', 'PENDING', 3, '1', %s, %s)
            """,
            (
                task_id,
                intent.case_id,
                intent.case_version,
                document_version_id,
                Jsonb(dict(task_input)),
                f"UPLOAD:{intent.id}",
            ),
        )
        return UploadRegistration(
            document_id=document_id,
            document_version_id=document_version_id,
            task_id=task_id,
            task_status="PENDING",
        )

    async def consume_intent(
        self,
        *,
        intent_id: UUID,
        actor_id: UUID,
        consumed_at: datetime,
        completion_idempotency_record_id: UUID,
    ) -> None:
        await self._connection.execute(
            """
            update public.upload_intents
            set status = 'CONSUMED', consumed_at = %s,
                completion_idempotency_record_id = %s
            where id = %s and assigned_officer_id = %s
              and status = 'OPEN' and consumed_at is null
            """,
            (consumed_at, completion_idempotency_record_id, intent_id, actor_id),
        )


class PostgresUnitOfWork:
    def __init__(self, connection_factory: ConnectionFactory, actor: ActorContext) -> None:
        self._connection_factory = connection_factory
        self._actor = actor
        self._connection_context: AbstractAsyncContextManager[DatabaseConnection] | None = None
        self._transaction_context: AbstractAsyncContextManager[None] | None = None
        self.cases: PostgresCaseRepository
        self.audit: PostgresAuditRepository
        self.uploads: PostgresUploadRepository

    async def __aenter__(self) -> Self:
        self._connection_context = self._connection_factory()
        connection = await self._connection_context.__aenter__()
        transaction_entered = False
        try:
            transaction_context = connection.transaction()
            self._transaction_context = transaction_context
            await transaction_context.__aenter__()
            transaction_entered = True

            await connection.execute("SET LOCAL ROLE creditops_api")

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
                    await transaction_context.__aexit__(
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
        self.uploads = PostgresUploadRepository(connection)
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
