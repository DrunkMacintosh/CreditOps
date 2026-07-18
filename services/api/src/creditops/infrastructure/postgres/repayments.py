"""Durable Postgres adapter for the stage-13 RepaymentLedger.

Every write is human-only and case/version scoped.  ``create_facility`` and
``record_event`` and ``record_collection_note`` are each ONE transaction that
writes the append-only row plus a ``HUMAN:<role>`` audit event together -- never
a partial write.  ``record_event`` is IDEMPOTENT: it inserts with
``on conflict (facility_id, external_reference) do nothing`` and, on a duplicate
delivery, re-selects and returns the EXISTING row with ``created=False`` so the
economic effect is written exactly once.  Nothing here confirms a gate, drives
orchestration, or executes any proposed collection action -- the ledger state is
recomputed elsewhere by the pure fold.

All identifiers and data are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from creditops.application.ports.repayments import (
    RecordedCollectionNote,
    RecordedFacility,
    RecordedRepaymentEvent,
)
from creditops.domain.repayments import Facility, RepaymentEvent
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_FACILITY_ARTIFACT = "FACILITY"
_EVENT_ARTIFACT = "REPAYMENT_EVENT"
_NOTE_ARTIFACT = "COLLECTION_NOTE"
_FACILITY_CREATED = "FACILITY_CREATED"
_EVENT_RECORDED = "REPAYMENT_EVENT_RECORDED"
_NOTE_RECORDED = "COLLECTION_NOTE_RECORDED"


class PostgresRepaymentLedgerRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- facilities -----------------------------------------------------------

    async def create_facility(
        self, *, facility: Facility, actor_id: UUID, actor_role: str
    ) -> RecordedFacility:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.facilities (
                      id, case_id, case_version, decision_id, principal,
                      annual_rate_percent, term_months, periodic_fee,
                      repayment_style, first_payment_date
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        facility.id,
                        facility.case_id,
                        facility.case_version,
                        facility.decision_id,
                        _text(facility.principal),
                        _text(facility.annual_rate_percent),
                        facility.term_months,
                        _text(facility.periodic_fee),
                        facility.repayment_style,
                        facility.first_payment_date,
                    ),
                )
                inserted = await cursor.fetchone()
                created_at = cast(datetime, inserted[0]) if inserted is not None else None
                await self._insert_audit(
                    connection,
                    case_id=facility.case_id,
                    case_version=facility.case_version,
                    event_type=_FACILITY_CREATED,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_type=_FACILITY_ARTIFACT,
                    artifact_id=facility.id,
                    event_data={
                        "facilityId": str(facility.id),
                        "decisionId": str(facility.decision_id),
                        "principal": _text(facility.principal),
                        "termMonths": facility.term_months,
                        "repaymentStyle": facility.repayment_style,
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return RecordedFacility(
            id=facility.id,
            case_id=facility.case_id,
            case_version=facility.case_version,
            decision_id=facility.decision_id,
            principal=facility.principal,
            annual_rate_percent=facility.annual_rate_percent,
            term_months=facility.term_months,
            periodic_fee=facility.periodic_fee,
            repayment_style=facility.repayment_style,
            first_payment_date=facility.first_payment_date,
            created_at=cast(datetime, created_at),
        )

    async def load_facility(
        self, facility_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedFacility | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, decision_id, principal,
                       annual_rate_percent, term_months, periodic_fee,
                       repayment_style, first_payment_date, created_at
                from public.facilities
                where id = %s and case_id = %s and case_version = %s
                """,
                (facility_id, case_id, case_version),
            )
            row = await cursor.fetchone()
        return _row_to_facility(row) if row is not None else None

    async def list_facilities(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedFacility, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, decision_id, principal,
                       annual_rate_percent, term_months, periodic_fee,
                       repayment_style, first_payment_date, created_at
                from public.facilities
                where case_id = %s and case_version = %s
                order by created_at asc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_facility(row) for row in rows)

    # -- repayment events -----------------------------------------------------

    async def record_event(
        self, *, event: RepaymentEvent, actor_id: UUID, actor_role: str
    ) -> tuple[RecordedRepaymentEvent, bool]:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.repayment_events (
                      id, facility_id, kind, amount, external_reference,
                      reversed_event_id, effective_date
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (facility_id, external_reference) do nothing
                    returning id, recorded_at
                    """,
                    (
                        event.id,
                        event.facility_id,
                        event.kind.value,
                        _text(event.amount),
                        event.external_reference,
                        event.reversed_event_id,
                        event.effective_date,
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    # Idempotent duplicate delivery: return the existing row, no
                    # second economic effect, no second audit event.
                    existing = await self._load_event_by_reference(
                        connection, event.facility_id, event.external_reference
                    )
                    if existing is None:
                        raise RuntimeError("repayment event idempotency row disappeared")
                    return existing, False
                recorded_at = cast(datetime, inserted[1])
                case_id, case_version = await self._facility_case(
                    connection, event.facility_id
                )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_EVENT_RECORDED,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_type=_EVENT_ARTIFACT,
                    artifact_id=event.id,
                    event_data={
                        "facilityId": str(event.facility_id),
                        "eventId": str(event.id),
                        "kind": event.kind.value,
                        "amount": _text(event.amount),
                        "externalReference": event.external_reference,
                        "reversedEventId": (
                            str(event.reversed_event_id)
                            if event.reversed_event_id is not None
                            else None
                        ),
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return (
            RecordedRepaymentEvent(
                id=event.id,
                facility_id=event.facility_id,
                kind=event.kind.value,
                amount=event.amount,
                external_reference=event.external_reference,
                reversed_event_id=event.reversed_event_id,
                effective_date=event.effective_date,
                recorded_at=recorded_at,
            ),
            True,
        )

    async def list_events(
        self, facility_id: UUID
    ) -> tuple[RecordedRepaymentEvent, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, facility_id, kind, amount, external_reference,
                       reversed_event_id, effective_date, recorded_at
                from public.repayment_events
                where facility_id = %s
                order by effective_date asc, recorded_at asc, id asc
                """,
                (facility_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_event(row) for row in rows)

    # -- collection notes -----------------------------------------------------

    async def record_collection_note(
        self,
        *,
        facility_id: UUID,
        case_id: UUID,
        case_version: int,
        note_kind: str,
        note_text_vi: str,
        proposed_action_vi: str | None,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedCollectionNote:
        note_id = uuid4()
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.collection_notes (
                      id, facility_id, case_id, case_version, note_kind,
                      note_text_vi, proposed_action_vi, author_id, author_role
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        note_id,
                        facility_id,
                        case_id,
                        case_version,
                        note_kind,
                        note_text_vi,
                        proposed_action_vi,
                        actor_id,
                        actor_role,
                    ),
                )
                inserted = await cursor.fetchone()
                created_at = cast(datetime, inserted[0]) if inserted is not None else None
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_NOTE_RECORDED,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_type=_NOTE_ARTIFACT,
                    artifact_id=note_id,
                    event_data={
                        "facilityId": str(facility_id),
                        "noteId": str(note_id),
                        "noteKind": note_kind,
                        "proposedAction": proposed_action_vi,
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return RecordedCollectionNote(
            id=note_id,
            facility_id=facility_id,
            case_id=case_id,
            case_version=case_version,
            note_kind=note_kind,
            note_text_vi=note_text_vi,
            proposed_action_vi=proposed_action_vi,
            author_id=actor_id,
            author_role=actor_role,
            created_at=cast(datetime, created_at),
        )

    async def list_collection_notes(
        self, facility_id: UUID
    ) -> tuple[RecordedCollectionNote, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, facility_id, case_id, case_version, note_kind,
                       note_text_vi, proposed_action_vi, author_id, author_role,
                       created_at
                from public.collection_notes
                where facility_id = %s
                order by created_at desc, id asc
                """,
                (facility_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_note(row) for row in rows)

    # -- helpers --------------------------------------------------------------

    @staticmethod
    async def _load_event_by_reference(
        connection: DatabaseConnection, facility_id: UUID, external_reference: str
    ) -> RecordedRepaymentEvent | None:
        cursor = await connection.execute(
            """
            select id, facility_id, kind, amount, external_reference,
                   reversed_event_id, effective_date, recorded_at
            from public.repayment_events
            where facility_id = %s and external_reference = %s
            """,
            (facility_id, external_reference),
        )
        row = await cursor.fetchone()
        return _row_to_event(row) if row is not None else None

    @staticmethod
    async def _facility_case(
        connection: DatabaseConnection, facility_id: UUID
    ) -> tuple[UUID, int]:
        cursor = await connection.execute(
            "select case_id, case_version from public.facilities where id = %s",
            (facility_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("repayment event references a missing facility")
        return cast(UUID, row[0]), int(cast(int, row[1]))

    @staticmethod
    async def _insert_audit(
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        event_type: str,
        actor_id: UUID,
        actor_role: str,
        artifact_type: str,
        artifact_id: UUID,
        event_data: dict[str, object],
    ) -> None:
        await connection.execute(
            """
            insert into public.audit_events (
              case_id, case_version, event_type, actor_type, actor_id,
              artifact_type, artifact_id, event_data
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                case_id,
                case_version,
                event_type,
                f"HUMAN:{actor_role}",
                actor_id,
                artifact_type,
                artifact_id,
                Jsonb(event_data),
            ),
        )


def _text(value: Decimal) -> str:
    """Exact Decimal-as-text (house money convention), no scientific notation."""
    return format(value, "f")


def _row_to_facility(row: Sequence[Any]) -> RecordedFacility:
    return RecordedFacility(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        decision_id=cast(UUID, row[3]),
        principal=Decimal(str(row[4])),
        annual_rate_percent=Decimal(str(row[5])),
        term_months=int(cast(int, row[6])),
        periodic_fee=Decimal(str(row[7])),
        repayment_style=str(row[8]),
        first_payment_date=cast(date, row[9]),
        created_at=cast(datetime, row[10]),
    )


def _row_to_event(row: Sequence[Any]) -> RecordedRepaymentEvent:
    return RecordedRepaymentEvent(
        id=cast(UUID, row[0]),
        facility_id=cast(UUID, row[1]),
        kind=str(row[2]),
        amount=Decimal(str(row[3])),
        external_reference=str(row[4]),
        reversed_event_id=cast("UUID | None", row[5]),
        effective_date=cast(date, row[6]),
        recorded_at=cast(datetime, row[7]),
    )


def _row_to_note(row: Sequence[Any]) -> RecordedCollectionNote:
    return RecordedCollectionNote(
        id=cast(UUID, row[0]),
        facility_id=cast(UUID, row[1]),
        case_id=cast(UUID, row[2]),
        case_version=int(cast(int, row[3])),
        note_kind=str(row[4]),
        note_text_vi=str(row[5]),
        proposed_action_vi=cast("str | None", row[6]),
        author_id=cast(UUID, row[7]),
        author_role=str(row[8]),
        created_at=cast(datetime, row[9]),
    )
