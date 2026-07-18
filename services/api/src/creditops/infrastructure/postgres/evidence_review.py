"""Durable Postgres adapter for the officer intake evidence review surface.

``load_document_review`` reads the current (latest) version of one document plus
its candidate facts and page regions -- and any confirmation already recorded --
so the API can enforce assignment (via the returned ``case_id``) and echo the
``expectedDocumentVersion`` the officer confirms against.  ``record_confirmations``
appends every candidate's disposition in ONE transaction: it locks the document
version and fails closed (``StaleDocumentVersionError``) if it is stale or no
longer in its reviewable stage, inserts each ``fact_confirmations`` row
idempotently (``on conflict (candidate_fact_id) do nothing`` + reselect), lets
the DB trigger derive each ``confirmed_facts`` row for supported dispositions,
and audits each freshly written confirmation.  The two case reads are strictly
version-scoped projections.  Every write is parameter-bound; nothing here can
satisfy a gate, resolve a conflict/gap, or record a credit decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from creditops.application.ports.evidence_review import (
    CandidateReview,
    ConfirmationInput,
    ConflictSourceView,
    ConflictView,
    DocumentReviewView,
    EvidenceFactView,
    RecordedConfirmations,
    StaleDocumentVersionError,
)
from creditops.application.use_cases.create_case import INTAKE_OFFICER_ROLE
from creditops.domain.enums import DocumentStage, FactDisposition
from creditops.domain.evidence import FactValue, PageRegion
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

#: Confirmation is a human intake action; provenance records the assigned intake
#: officer, never an agent.
_ACTOR_TYPE = f"HUMAN:{INTAKE_OFFICER_ROLE}"
#: The authority provenance recorded on every confirmation (mirrors the intake
#: domain's ``ConfirmationAuthority.source``); the DB trigger independently
#: verifies the assignment is active for the authority interval.
_AUTHORITY_SOURCE = "CASE_ASSIGNMENT"
_CONFIRMATION_EVENT_TYPE = "FACT_CONFIRMATION_RECORDED"
_CONFIRMATION_ARTIFACT_TYPE = "FACT_CONFIRMATION"
#: Dispositions that derive a ``confirmed_facts`` row (ABSENT/UNREADABLE record
#: the confirmation but assert no value, so no confirmed fact is derived).
_DERIVES_CONFIRMED_FACT = frozenset(
    {FactDisposition.ACCEPTED, FactDisposition.CORRECTED}
)


class PostgresEvidenceReviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def load_document_review(
        self, document_id: UUID
    ) -> DocumentReviewView | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                "select id, case_id from public.documents where id = %s",
                (document_id,),
            )
            document_row = await cursor.fetchone()
            if document_row is None:
                return None
            case_id = cast(UUID, document_row[1])

            cursor = await connection.execute(
                """
                select id, case_version, version, stage, original_filename
                from public.document_versions
                where document_id = %s and case_id = %s
                order by version desc
                limit 1
                """,
                (document_id, case_id),
            )
            version_row = await cursor.fetchone()
            if version_row is None:
                return None
            document_version_id = cast(UUID, version_row[0])
            case_version = int(version_row[1])
            document_version = int(version_row[2])
            stage = DocumentStage(str(version_row[3]))
            file_name = cast("str | None", version_row[4])

            cursor = await connection.execute(
                """
                select max(page_number)
                from public.page_regions
                where document_version_id = %s and case_id = %s and case_version = %s
                """,
                (document_version_id, case_id, case_version),
            )
            page_row = await cursor.fetchone()
            page_count = (
                int(page_row[0]) if page_row is not None and page_row[0] is not None else None
            )

            cursor = await connection.execute(
                """
                select c.id, c.field_key, c.proposed_value, c.confidence,
                       r.page_number, r.x, r.y, r.width, r.height,
                       fc.disposition, fc.corrected_value, fc.confirmed_at
                from public.candidate_facts as c
                join public.page_regions as r on r.id = c.page_region_id
                left join public.fact_confirmations as fc
                  on fc.candidate_fact_id = c.id
                where c.document_version_id = %s and c.case_id = %s
                  and c.case_version = %s and c.stale_at is null
                order by c.created_at, c.id
                """,
                (document_version_id, case_id, case_version),
            )
            candidates = tuple(
                CandidateReview(
                    candidate_id=cast(UUID, row[0]),
                    field_key=str(row[1]),
                    proposed_value=cast(FactValue, row[2]),
                    confidence=float(row[3]),
                    source=PageRegion(
                        page=int(row[4]),
                        x=float(row[5]),
                        y=float(row[6]),
                        width=float(row[7]),
                        height=float(row[8]),
                    ),
                    disposition=(
                        FactDisposition(str(row[9])) if row[9] is not None else None
                    ),
                    corrected_value=cast("FactValue | None", row[10]),
                    confirmed_at=cast("datetime | None", row[11]),
                )
                for row in await cursor.fetchall()
            )

        return DocumentReviewView(
            document_id=document_id,
            case_id=case_id,
            case_version=case_version,
            document_version_id=document_version_id,
            document_version=document_version,
            stage=stage,
            file_name=file_name,
            page_count=page_count,
            candidates=candidates,
        )

    async def record_confirmations(
        self,
        *,
        document_version_id: UUID,
        confirmations: tuple[ConfirmationInput, ...],
        actor_id: UUID,
        expected_document_stage: DocumentStage,
    ) -> RecordedConfirmations:
        confirmation_ids: list[UUID] = []
        confirmed_fact_ids: list[UUID] = []
        created_any = False
        async with self._connection_factory() as connection:
            async with connection.transaction():
                # Lock the target version and fail closed if it drifted between
                # the officer's read and this write.
                cursor = await connection.execute(
                    """
                    select case_id, case_version, stage, stale_at
                    from public.document_versions
                    where id = %s
                    for update
                    """,
                    (document_version_id,),
                )
                version_row = await cursor.fetchone()
                if version_row is None or version_row[3] is not None:
                    raise StaleDocumentVersionError(document_version_id)
                if str(version_row[2]) != expected_document_stage.value:
                    raise StaleDocumentVersionError(document_version_id)
                case_id = cast(UUID, version_row[0])
                case_version = int(version_row[1])

                for confirmation in confirmations:
                    confirmation_id, created = await self._record_one(
                        connection,
                        case_id=case_id,
                        case_version=case_version,
                        actor_id=actor_id,
                        document_version_id=document_version_id,
                        confirmation=confirmation,
                    )
                    confirmation_ids.append(confirmation_id)
                    created_any = created_any or created
                    if not created:
                        continue
                    if confirmation.disposition in _DERIVES_CONFIRMED_FACT:
                        confirmed_fact_id = await self._derive_confirmed_fact(
                            connection,
                            candidate_id=confirmation.candidate_id,
                            confirmation_id=confirmation_id,
                        )
                        if confirmed_fact_id is not None:
                            confirmed_fact_ids.append(confirmed_fact_id)
                    await self._append_confirmation_audit(
                        connection,
                        case_id=case_id,
                        case_version=case_version,
                        actor_id=actor_id,
                        document_version_id=document_version_id,
                        confirmation=confirmation,
                        confirmation_id=confirmation_id,
                    )

        return RecordedConfirmations(
            confirmation_ids=tuple(confirmation_ids),
            confirmed_fact_ids=tuple(confirmed_fact_ids),
            created=created_any,
        )

    async def _record_one(
        self,
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        actor_id: UUID,
        document_version_id: UUID,
        confirmation: ConfirmationInput,
    ) -> tuple[UUID, bool]:
        corrected = (
            Jsonb(confirmation.corrected_value)
            if confirmation.disposition is FactDisposition.CORRECTED
            else None
        )
        # ``now()`` (transaction start) satisfies authority_granted_at <=
        # confirmed_at <= created_at (default clock_timestamp() >= now()); the
        # actor is the assigned officer (DB check + FK enforce this).
        cursor = await connection.execute(
            """
            insert into public.fact_confirmations (
              id, case_id, case_version, candidate_fact_id, disposition,
              corrected_value, actor_id, assigned_officer_id, authority_source,
              authority_granted_at, confirmed_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
            on conflict (candidate_fact_id) do nothing
            returning id
            """,
            (
                uuid4(),
                case_id,
                case_version,
                confirmation.candidate_id,
                confirmation.disposition.value,
                corrected,
                actor_id,
                actor_id,
                _AUTHORITY_SOURCE,
            ),
        )
        inserted = await cursor.fetchone()
        if inserted is not None:
            return cast(UUID, inserted[0]), True

        cursor = await connection.execute(
            """
            select id from public.fact_confirmations
            where candidate_fact_id = %s
            """,
            (confirmation.candidate_id,),
        )
        existing = await cursor.fetchone()
        if existing is None:  # pragma: no cover - conflict without a row is impossible
            raise RuntimeError("fact confirmation idempotency row disappeared")
        return cast(UUID, existing[0]), False

    async def _derive_confirmed_fact(
        self,
        connection: DatabaseConnection,
        *,
        candidate_id: UUID,
        confirmation_id: UUID,
    ) -> UUID | None:
        # The ``derive_and_protect_confirmed_fact`` BEFORE INSERT trigger fills
        # every authoritative field from the candidate + confirmation; we supply
        # only the identity so no caller value can drift.
        cursor = await connection.execute(
            """
            insert into public.confirmed_facts (id, candidate_fact_id, confirmation_id)
            values (%s, %s, %s)
            on conflict (confirmation_id) do nothing
            returning id
            """,
            (uuid4(), candidate_id, confirmation_id),
        )
        row = await cursor.fetchone()
        return cast(UUID, row[0]) if row is not None else None

    async def _append_confirmation_audit(
        self,
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        actor_id: UUID,
        document_version_id: UUID,
        confirmation: ConfirmationInput,
        confirmation_id: UUID,
    ) -> None:
        event_data: dict[str, object] = {
            "candidateId": str(confirmation.candidate_id),
            "documentVersionId": str(document_version_id),
            "disposition": confirmation.disposition.value,
        }
        if confirmation.rationale is not None:
            event_data["rationale"] = confirmation.rationale
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
                _CONFIRMATION_EVENT_TYPE,
                _ACTOR_TYPE,
                actor_id,
                _CONFIRMATION_ARTIFACT_TYPE,
                confirmation_id,
                Jsonb(event_data),
            ),
        )

    async def load_case_evidence(
        self, case_id: UUID, case_version: int
    ) -> tuple[EvidenceFactView, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select cf.id, cf.candidate_fact_id, cf.confirmation_id,
                       cf.document_version_id, cf.field_key, cf.value,
                       cf.candidate_value, cf.confirmed_at, cf.stale_at,
                       r.page_number, r.x, r.y, r.width, r.height
                from public.confirmed_facts as cf
                join public.page_regions as r on r.id = cf.page_region_id
                where cf.case_id = %s and cf.case_version = %s
                order by cf.created_at, cf.id
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(
            EvidenceFactView(
                id=cast(UUID, row[0]),
                case_id=case_id,
                case_version=case_version,
                candidate_id=cast(UUID, row[1]),
                confirmation_id=cast(UUID, row[2]),
                document_version_id=cast(UUID, row[3]),
                field_key=str(row[4]),
                value=cast(FactValue, row[5]),
                candidate_value=cast(FactValue, row[6]),
                confirmed_at=cast(datetime, row[7]),
                stale=row[8] is not None,
                source=PageRegion(
                    page=int(row[9]),
                    x=float(row[10]),
                    y=float(row[11]),
                    width=float(row[12]),
                    height=float(row[13]),
                ),
            )
            for row in rows
        )

    async def load_case_conflicts(
        self, case_id: UUID, case_version: int
    ) -> tuple[ConflictView, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select ec.id, ec.field_key, ec.created_at, ec.stale_at, ec.status,
                       lf.document_version_id, lf.value,
                       lr.page_number, lr.x, lr.y, lr.width, lr.height,
                       rf.document_version_id, rf.value,
                       rr.page_number, rr.x, rr.y, rr.width, rr.height
                from public.evidence_conflicts as ec
                join public.confirmed_facts as lf on lf.id = ec.left_confirmed_fact_id
                join public.page_regions as lr on lr.id = lf.page_region_id
                join public.confirmed_facts as rf on rf.id = ec.right_confirmed_fact_id
                join public.page_regions as rr on rr.id = rf.page_region_id
                where ec.case_id = %s and ec.case_version = %s
                order by ec.created_at, ec.id
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(
            ConflictView(
                id=cast(UUID, row[0]),
                case_id=case_id,
                case_version=case_version,
                field_key=str(row[1]),
                detected_at=cast("datetime | None", row[2]),
                stale=row[3] is not None or str(row[4]) == "STALE",
                sources=(
                    ConflictSourceView(
                        document_version_id=cast(UUID, row[5]),
                        value=cast(FactValue, row[6]),
                        source=PageRegion(
                            page=int(row[7]),
                            x=float(row[8]),
                            y=float(row[9]),
                            width=float(row[10]),
                            height=float(row[11]),
                        ),
                    ),
                    ConflictSourceView(
                        document_version_id=cast(UUID, row[12]),
                        value=cast(FactValue, row[13]),
                        source=PageRegion(
                            page=int(row[14]),
                            x=float(row[15]),
                            y=float(row[16]),
                            width=float(row[17]),
                            height=float(row[18]),
                        ),
                    ),
                ),
            )
            for row in rows
        )
