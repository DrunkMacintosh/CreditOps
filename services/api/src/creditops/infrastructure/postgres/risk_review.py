"""Durable Postgres adapter for the Independent Risk Review Agent (Checker).

Maker-output immutability at the checker boundary: ``load_maker_outputs`` is
the ONLY method that reads ``public.underwriting_assessments`` or
``public.legal_compliance_assessments``, and it issues ``select`` statements
exclusively -- there is no ``insert``/``update``/``delete`` against either
table anywhere in this module (see ``tests/unit/risk_review/
test_port_surface.py`` for the source-text-level proof).  Every other write
here is bounded: an append-only checker-assessment insert deduplicated per
(case, case version, task), first-class append-only challenge rows,
PROVISIONAL evidence-gap inserts, one immutable checker->operations handoff,
append-only human challenge/assessment dispositions, and agent audit events.
Nothing here can satisfy a gate, resolve a gap, confirm a fact, or record any
credit decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.risk_review import (
    ChallengeDispositionRecord,
    CheckerEvidenceView,
    EvidenceFact,
    LatestRiskReviewRecord,
    MakerOutputsView,
    OpenGapRecord,
    PersistedCheckerOutput,
    ProvisionalGapRecord,
)
from creditops.application.risk_review.checker import OPERATIONS_HANDOFF_STATE
from creditops.domain.legal import LegalComplianceAssessment
from creditops.domain.risk_review import RISK_REVIEW_AGENT_ROLE, RiskReviewAssessment
from creditops.domain.underwriting import GapBlockingLevel, UnderwritingAssessment
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_ACTOR_TYPE = f"AGENT:{RISK_REVIEW_AGENT_ROLE}"
_RISK_REVIEW_HANDOFF_STATE = "READY_FOR_RISK_REVIEW"


class PostgresRiskReviewRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- evidence view (Confirmed Facts only) --------------------------------

    async def load_evidence_view(self, case_id: UUID) -> CheckerEvidenceView | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                "select case_version from public.credit_cases where id = %s",
                (case_id,),
            )
            case_row = await cursor.fetchone()
            if case_row is None:
                return None
            case_version = int(case_row[0])

            cursor = await connection.execute(
                """
                select id, field_key, value, document_version_id
                from public.confirmed_facts
                where case_id = %s and case_version = %s and stale_at is null
                order by created_at
                """,
                (case_id, case_version),
            )
            facts = tuple(
                EvidenceFact(
                    confirmed_fact_id=cast(UUID, row[0]),
                    field_key=str(row[1]),
                    value=cast("str | int | float | bool", row[2]),
                    document_version_id=cast(UUID, row[3]),
                )
                for row in await cursor.fetchall()
            )
        return CheckerEvidenceView(
            case_id=case_id,
            case_version=case_version,
            built_at=datetime.now(UTC),
            confirmed_facts=facts,
        )

    # -- READ-ONLY maker output access ---------------------------------------
    # The only method in this repository that ever selects from
    # underwriting_assessments / legal_compliance_assessments.  No write
    # statement against either table appears anywhere in this class.

    async def load_maker_outputs(self, case_id: UUID, case_version: int) -> MakerOutputsView:
        async with self._connection_factory() as connection:
            underwriting, underwriting_execution_id, underwriting_handoff_id = (
                await self._load_underwriting(connection, case_id, case_version)
            )
            legal, legal_execution_id, legal_handoff_id = await self._load_legal(
                connection, case_id, case_version
            )
        return MakerOutputsView(
            underwriting=underwriting,
            underwriting_execution_id=underwriting_execution_id,
            underwriting_handoff_id=underwriting_handoff_id,
            legal=legal,
            legal_execution_id=legal_execution_id,
            legal_handoff_id=legal_handoff_id,
        )

    @staticmethod
    async def _load_underwriting(
        connection: DatabaseConnection, case_id: UUID, case_version: int
    ) -> tuple[UnderwritingAssessment | None, UUID | None, UUID | None]:
        cursor = await connection.execute(
            """
            select a.id, a.task_id, a.execution_id, a.assessment
            from public.underwriting_assessments as a
            where a.case_id = %s and a.case_version = %s
            order by a.created_at desc
            limit 1
            """,
            (case_id, case_version),
        )
        row = await cursor.fetchone()
        if row is None:
            return None, None, None
        task_id = cast(UUID, row[1])
        handoff_id = await PostgresRiskReviewRepository._find_ready_handoff(
            connection, case_id, case_version, task_id, _RISK_REVIEW_HANDOFF_STATE
        )
        if handoff_id is None:
            # No READY_FOR_RISK_REVIEW handoff for this task: not reviewable
            # yet, regardless of the assessment row's presence.
            return None, None, None
        assessment = UnderwritingAssessment.model_validate(row[3])
        return assessment, cast(UUID, row[2]), handoff_id

    @staticmethod
    async def _load_legal(
        connection: DatabaseConnection, case_id: UUID, case_version: int
    ) -> tuple[LegalComplianceAssessment | None, UUID | None, UUID | None]:
        cursor = await connection.execute(
            """
            select a.id, a.task_id, a.execution_id, a.assessment
            from public.legal_compliance_assessments as a
            where a.case_id = %s and a.case_version = %s
            order by a.created_at desc
            limit 1
            """,
            (case_id, case_version),
        )
        row = await cursor.fetchone()
        if row is None:
            return None, None, None
        task_id = cast(UUID, row[1])
        handoff_id = await PostgresRiskReviewRepository._find_ready_handoff(
            connection, case_id, case_version, task_id, _RISK_REVIEW_HANDOFF_STATE
        )
        if handoff_id is None:
            return None, None, None
        assessment = LegalComplianceAssessment.model_validate(row[3])
        return assessment, cast(UUID, row[2]), handoff_id

    @staticmethod
    async def _find_ready_handoff(
        connection: DatabaseConnection,
        case_id: UUID,
        case_version: int,
        source_task_id: UUID,
        state: str,
    ) -> UUID | None:
        cursor = await connection.execute(
            """
            select id from public.handoffs
            where case_id = %s and case_version = %s and source_task_id = %s
              and state = %s and stale_at is null
            order by created_at desc
            limit 1
            """,
            (case_id, case_version, source_task_id, state),
        )
        row = await cursor.fetchone()
        return cast(UUID, row[0]) if row is not None else None

    # -- visibility recheck source of truth ----------------------------------

    async def load_open_gaps(
        self, case_id: UUID, case_version: int
    ) -> tuple[OpenGapRecord, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, missing_information_vi, blocking_level, status
                from public.evidence_gaps
                where case_id = %s and case_version = %s
                  and status in ('PROVISIONAL', 'FORMAL')
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(
            OpenGapRecord(
                gap_id=cast(UUID, row[0]),
                missing_information_vi=str(row[1]),
                blocking_level=GapBlockingLevel(str(row[2])),
                status=str(row[3]),
            )
            for row in rows
        )

    # -- checker output read models ------------------------------------------

    async def load_latest_assessment(self, case_id: UUID) -> LatestRiskReviewRecord | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select a.id, a.case_id, a.case_version, a.execution_id,
                       a.agent_role, a.prompt_version, a.created_at,
                       a.assessment, a.task_id
                from public.risk_review_assessments as a
                where a.case_id = %s
                order by a.created_at desc
                limit 1
                """,
                (case_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            task_id = cast(UUID, row[8])
            case_version = int(row[2])
            handoff_id = await self._find_ready_handoff(
                connection, case_id, case_version, task_id, OPERATIONS_HANDOFF_STATE
            )
            handoff_row = None
            if handoff_id is not None:
                cursor = await connection.execute(
                    "select state, created_at from public.handoffs where id = %s",
                    (handoff_id,),
                )
                handoff_row = await cursor.fetchone()
        return LatestRiskReviewRecord(
            assessment_id=cast(UUID, row[0]),
            case_id=cast(UUID, row[1]),
            case_version=case_version,
            execution_id=cast(UUID, row[3]),
            agent_role=str(row[4]),
            prompt_version=str(row[5]),
            created_at=cast(datetime, row[6]),
            assessment=cast("dict[str, object]", row[7]),
            handoff_id=handoff_id,
            handoff_state=str(handoff_row[0]) if handoff_row else None,
            handoff_created_at=cast(datetime, handoff_row[1]) if handoff_row else None,
        )

    async def load_dispositions(
        self, case_id: UUID, case_version: int
    ) -> tuple[ChallengeDispositionRecord, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, assessment_id, challenge_id, disposition_type,
                       rationale_vi, actor_id, actor_role, created_at
                from public.challenge_dispositions
                where case_id = %s and case_version = %s
                order by created_at
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(
            ChallengeDispositionRecord(
                id=cast(UUID, row[0]),
                assessment_id=cast(UUID, row[1]),
                challenge_id=cast("UUID | None", row[2]),
                disposition_type=str(row[3]),
                rationale_vi=str(row[4]),
                actor_id=cast(UUID, row[5]),
                actor_role=str(row[6]),
                created_at=cast(datetime, row[7]),
            )
            for row in rows
        )

    async def find_persisted(
        self, *, case_id: UUID, case_version: int, task_id: UUID
    ) -> PersistedCheckerOutput | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id from public.risk_review_assessments
                where case_id = %s and case_version = %s and task_id = %s
                """,
                (case_id, case_version, task_id),
            )
            assessment_row = await cursor.fetchone()
            if assessment_row is None:
                return None
            assessment_id = cast(UUID, assessment_row[0])
            handoff_id = await self._find_ready_handoff(
                connection, case_id, case_version, task_id, OPERATIONS_HANDOFF_STATE
            )
            challenge_ids = await self._find_challenge_ids(connection, assessment_id)
        if handoff_id is None:
            return None
        return PersistedCheckerOutput(
            assessment_id=assessment_id,
            handoff_id=handoff_id,
            challenge_ids=challenge_ids,
            handoff_state=OPERATIONS_HANDOFF_STATE,
            created=False,
        )

    async def persist_assessment(
        self,
        *,
        assessment: RiskReviewAssessment,
        handoff_id: UUID,
        handoff_state: str,
        gaps: tuple[ProvisionalGapRecord, ...],
    ) -> PersistedCheckerOutput:
        provenance = assessment.provenance
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.risk_review_assessments (
                      id, case_id, case_version, task_id, execution_id,
                      prompt_version, model_id, endpoint_id, assessment,
                      assessment_schema_version, evidence_view_built_at
                    ) values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      'risk-review-assessment-v1', %s
                    )
                    on conflict (case_id, case_version, task_id) do nothing
                    returning id
                    """,
                    (
                        assessment.id,
                        provenance.case_id,
                        provenance.case_version,
                        provenance.task_id,
                        provenance.execution_id,
                        provenance.prompt_version,
                        provenance.model_id,
                        provenance.endpoint_id,
                        Jsonb(assessment.model_dump(mode="json")),
                        provenance.evidence_view_built_at,
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    cursor = await connection.execute(
                        """
                        select id from public.risk_review_assessments
                        where case_id = %s and case_version = %s and task_id = %s
                        """,
                        (provenance.case_id, provenance.case_version, provenance.task_id),
                    )
                    existing = await cursor.fetchone()
                    if existing is None:
                        raise RuntimeError("assessment idempotency row disappeared")
                    existing_id = cast(UUID, existing[0])
                    existing_handoff = await self._find_ready_handoff(
                        connection,
                        provenance.case_id,
                        provenance.case_version,
                        provenance.task_id,
                        handoff_state,
                    )
                    if existing_handoff is None:
                        raise RuntimeError("persisted assessment has no handoff")
                    return PersistedCheckerOutput(
                        assessment_id=existing_id,
                        handoff_id=existing_handoff,
                        challenge_ids=await self._find_challenge_ids(connection, existing_id),
                        handoff_state=handoff_state,
                        created=False,
                    )

                for challenge in assessment.challenges:
                    await connection.execute(
                        """
                        insert into public.risk_review_challenges (
                          id, assessment_id, case_id, case_version,
                          target_maker_source, target_maker_assessment_id,
                          target_section_path, challenge_type, statement_vi,
                          citations, severity, confidence, raised_by
                        ) values (
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            challenge.id,
                            assessment.id,
                            provenance.case_id,
                            provenance.case_version,
                            challenge.target.maker_source.value,
                            challenge.target.maker_assessment_id,
                            challenge.target.section_path,
                            challenge.challenge_type.value,
                            challenge.statement_vi,
                            Jsonb(
                                [c.model_dump(mode="json") for c in challenge.citations]
                            ),
                            challenge.severity.value,
                            challenge.confidence.value,
                            challenge.raised_by.value,
                        ),
                    )

                for gap in gaps:
                    await connection.execute(
                        """
                        insert into public.evidence_gaps (
                          id, case_id, case_version, affected_task_id, status,
                          blocking_level, issue_vi, missing_information_vi,
                          suggested_evidence_vi, created_by_type
                        ) values (
                          %s, %s, %s, %s, 'PROVISIONAL', %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            uuid4(),
                            provenance.case_id,
                            provenance.case_version,
                            provenance.task_id,
                            gap.blocking_level.value,
                            gap.issue_vi,
                            gap.missing_information_vi,
                            Jsonb(list(gap.suggested_evidence_vi)),
                            _ACTOR_TYPE,
                        ),
                    )

                await connection.execute(
                    """
                    insert into public.handoffs (
                      id, case_id, case_version, source_task_id, state,
                      handoff_data, created_by_type
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (id) do nothing
                    """,
                    (
                        handoff_id,
                        provenance.case_id,
                        provenance.case_version,
                        provenance.task_id,
                        handoff_state,
                        Jsonb(
                            {
                                "assessmentId": str(assessment.id),
                                "executionId": str(provenance.execution_id),
                                "schemaVersion": "risk-review-assessment-v1",
                            }
                        ),
                        _ACTOR_TYPE,
                    ),
                )
        return PersistedCheckerOutput(
            assessment_id=assessment.id,
            handoff_id=handoff_id,
            challenge_ids=tuple(challenge.id for challenge in assessment.challenges),
            handoff_state=handoff_state,
            created=True,
        )

    async def record_disposition(
        self,
        *,
        disposition_id: UUID,
        assessment_id: UUID,
        challenge_id: UUID | None,
        disposition_type: str,
        rationale_vi: str,
        actor_id: UUID,
        actor_role: str,
    ) -> ChallengeDispositionRecord:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    select case_id, case_version from public.risk_review_assessments
                    where id = %s
                    """,
                    (assessment_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError(f"unknown risk review assessment: {assessment_id}")
                case_id, case_version = cast(UUID, row[0]), int(row[1])
                cursor = await connection.execute(
                    """
                    insert into public.challenge_dispositions (
                      id, assessment_id, case_id, case_version, challenge_id,
                      disposition_type, rationale_vi, actor_id, actor_role
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        disposition_id,
                        assessment_id,
                        case_id,
                        case_version,
                        challenge_id,
                        disposition_type,
                        rationale_vi,
                        actor_id,
                        actor_role,
                    ),
                )
                created = await cursor.fetchone()
        created_at = cast(datetime, created[0]) if created is not None else datetime.now(UTC)
        return ChallengeDispositionRecord(
            id=disposition_id,
            assessment_id=assessment_id,
            challenge_id=challenge_id,
            disposition_type=disposition_type,
            rationale_vi=rationale_vi,
            actor_id=actor_id,
            actor_role=actor_role,
            created_at=created_at,
        )

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        event_data = dict(event.event_data)
        event_data["executionId"] = str(event.execution_id)
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    insert into public.audit_events (
                      case_id, case_version, event_type, actor_type, actor_id,
                      artifact_type, artifact_id, event_data
                    ) values (%s, %s, %s, %s, null, %s, %s, %s)
                    """,
                    (
                        event.case_id,
                        event.case_version,
                        event.event_type,
                        _ACTOR_TYPE,
                        event.artifact_type,
                        event.artifact_id,
                        Jsonb(event_data),
                    ),
                )

    @staticmethod
    async def _find_challenge_ids(
        connection: DatabaseConnection, assessment_id: UUID
    ) -> tuple[UUID, ...]:
        cursor = await connection.execute(
            """
            select id from public.risk_review_challenges
            where assessment_id = %s order by created_at
            """,
            (assessment_id,),
        )
        rows = await cursor.fetchall()
        return tuple(cast(UUID, row[0]) for row in rows)
