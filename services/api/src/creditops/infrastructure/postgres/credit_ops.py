"""Durable Postgres adapter for the Credit Operations Agent.

Upstream-output immutability at the credit-ops boundary: ``load_upstream_view``
is the ONLY method that reads ``public.underwriting_assessments``,
``public.legal_compliance_assessments``, or ``public.risk_review_assessments``,
and it issues ``select`` statements exclusively -- there is no
``insert``/``update``/``delete`` against any of those three tables anywhere in
this module (see ``tests/unit/credit_ops/test_postgres_adapter_boundary.py``
for the source-text-level proof).  Every other write here is bounded: an
append-only package insert deduplicated per (case, case version, task), one
immutable operations->human-decision handoff, append-only action-authorization
rows, append-only document-request-approval rows, and agent audit events.
Nothing here can satisfy a gate, resolve a gap, confirm a fact, send a
customer communication, or execute a proposed action.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.credit_ops.assembler import HUMAN_DECISION_HANDOFF_STATE
from creditops.application.ports.credit_ops import (
    ActionAuthorizationRecord,
    ChallengeDispositionSummary,
    CreditOpsUpstreamView,
    DocumentRequestApprovalRecord,
    LatestCreditOpsPackageRecord,
    OpenGapRecord,
    PersistedCreditOpsOutput,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.credit_ops import CREDIT_OPS_AGENT_ROLE, CreditOpsPackage
from creditops.domain.legal import LegalComplianceAssessment
from creditops.domain.risk_review import RiskReviewAssessment
from creditops.domain.underwriting import GapBlockingLevel, UnderwritingAssessment
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_ACTOR_TYPE = f"AGENT:{CREDIT_OPS_AGENT_ROLE}"
_INTAKE_HANDOFF_STATE = "READY_FOR_SPECIALIST_REVIEW"
_RISK_REVIEW_HANDOFF_STATE = "READY_FOR_RISK_REVIEW"
_OPERATIONS_HANDOFF_STATE = "READY_FOR_OPERATIONS"


class PostgresCreditOpsRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- READ-ONLY upstream access -------------------------------------------
    # The only method in this repository that ever selects from
    # underwriting_assessments / legal_compliance_assessments /
    # risk_review_assessments.  No write statement against any of the three
    # appears anywhere in this class.

    async def load_upstream_view(self, case_id: UUID) -> CreditOpsUpstreamView | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                "select case_version from public.credit_cases where id = %s",
                (case_id,),
            )
            case_row = await cursor.fetchone()
            if case_row is None:
                return None
            case_version = int(case_row[0])

            intake_handoff_id = await self._find_handoff_by_state(
                connection, case_id, case_version, _INTAKE_HANDOFF_STATE
            )
            underwriting, underwriting_execution_id, underwriting_handoff_id = (
                await self._load_underwriting(connection, case_id, case_version)
            )
            legal, legal_execution_id, legal_handoff_id = await self._load_legal(
                connection, case_id, case_version
            )
            risk_review, risk_review_execution_id, risk_review_handoff_id = (
                await self._load_risk_review(connection, case_id, case_version)
            )
        return CreditOpsUpstreamView(
            case_id=case_id,
            case_version=case_version,
            built_at=datetime.now(UTC),
            has_intake_handoff=intake_handoff_id is not None,
            intake_handoff_id=intake_handoff_id,
            underwriting=underwriting,
            underwriting_execution_id=underwriting_execution_id,
            underwriting_handoff_id=underwriting_handoff_id,
            legal=legal,
            legal_execution_id=legal_execution_id,
            legal_handoff_id=legal_handoff_id,
            risk_review=risk_review,
            risk_review_execution_id=risk_review_execution_id,
            risk_review_handoff_id=risk_review_handoff_id,
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
        handoff_id = await PostgresCreditOpsRepository._find_ready_handoff(
            connection, case_id, case_version, task_id, _RISK_REVIEW_HANDOFF_STATE
        )
        if handoff_id is None:
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
        handoff_id = await PostgresCreditOpsRepository._find_ready_handoff(
            connection, case_id, case_version, task_id, _RISK_REVIEW_HANDOFF_STATE
        )
        if handoff_id is None:
            return None, None, None
        assessment = LegalComplianceAssessment.model_validate(row[3])
        return assessment, cast(UUID, row[2]), handoff_id

    @staticmethod
    async def _load_risk_review(
        connection: DatabaseConnection, case_id: UUID, case_version: int
    ) -> tuple[RiskReviewAssessment | None, UUID | None, UUID | None]:
        cursor = await connection.execute(
            """
            select a.id, a.task_id, a.execution_id, a.assessment
            from public.risk_review_assessments as a
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
        handoff_id = await PostgresCreditOpsRepository._find_ready_handoff(
            connection, case_id, case_version, task_id, _OPERATIONS_HANDOFF_STATE
        )
        if handoff_id is None:
            return None, None, None
        assessment = RiskReviewAssessment.model_validate(row[3])
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

    @staticmethod
    async def _find_handoff_by_state(
        connection: DatabaseConnection, case_id: UUID, case_version: int, state: str
    ) -> UUID | None:
        cursor = await connection.execute(
            """
            select id from public.handoffs
            where case_id = %s and case_version = %s and state = %s and stale_at is null
            order by created_at desc
            limit 1
            """,
            (case_id, case_version, state),
        )
        row = await cursor.fetchone()
        return cast(UUID, row[0]) if row is not None else None

    # -- document-request consolidation source of truth ----------------------

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

    async def load_dispositions(
        self, case_id: UUID, case_version: int
    ) -> tuple[ChallengeDispositionSummary, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select challenge_id, disposition_type
                from public.challenge_dispositions
                where case_id = %s and case_version = %s
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(
            ChallengeDispositionSummary(
                challenge_id=cast("UUID | None", row[0]), disposition_type=str(row[1])
            )
            for row in rows
        )

    # -- credit-ops package read models --------------------------------------

    async def load_latest_package(self, case_id: UUID) -> LatestCreditOpsPackageRecord | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, execution_id, agent_role,
                       prompt_version, created_at, package, task_id
                from public.credit_ops_packages
                where case_id = %s
                order by created_at desc
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
                connection, case_id, case_version, task_id, HUMAN_DECISION_HANDOFF_STATE
            )
            handoff_row = None
            if handoff_id is not None:
                cursor = await connection.execute(
                    "select state, created_at from public.handoffs where id = %s",
                    (handoff_id,),
                )
                handoff_row = await cursor.fetchone()
        return LatestCreditOpsPackageRecord(
            package_id=cast(UUID, row[0]),
            case_id=cast(UUID, row[1]),
            case_version=case_version,
            execution_id=cast(UUID, row[3]),
            agent_role=str(row[4]),
            prompt_version=str(row[5]),
            created_at=cast(datetime, row[6]),
            package=cast("dict[str, object]", row[7]),
            handoff_id=handoff_id,
            handoff_state=str(handoff_row[0]) if handoff_row else None,
            handoff_created_at=cast(datetime, handoff_row[1]) if handoff_row else None,
        )

    async def load_action_authorizations(
        self, package_id: UUID
    ) -> tuple[ActionAuthorizationRecord, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, package_id, action_id, actor_id, actor_role,
                       rationale_vi, created_at
                from public.ops_action_authorizations
                where package_id = %s
                order by created_at
                """,
                (package_id,),
            )
            rows = await cursor.fetchall()
        return tuple(
            ActionAuthorizationRecord(
                id=cast(UUID, row[0]),
                package_id=cast(UUID, row[1]),
                action_id=cast(UUID, row[2]),
                actor_id=cast(UUID, row[3]),
                actor_role=str(row[4]),
                rationale_vi=str(row[5]),
                created_at=cast(datetime, row[6]),
            )
            for row in rows
        )

    async def load_document_request_approvals(
        self, package_id: UUID
    ) -> tuple[DocumentRequestApprovalRecord, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, package_id, request_id, actor_id, actor_role,
                       rationale_vi, created_at
                from public.document_request_approvals
                where package_id = %s
                order by created_at
                """,
                (package_id,),
            )
            rows = await cursor.fetchall()
        return tuple(
            DocumentRequestApprovalRecord(
                id=cast(UUID, row[0]),
                package_id=cast(UUID, row[1]),
                request_id=cast(UUID, row[2]),
                actor_id=cast(UUID, row[3]),
                actor_role=str(row[4]),
                rationale_vi=str(row[5]),
                created_at=cast(datetime, row[6]),
            )
            for row in rows
        )

    async def find_persisted(
        self, *, case_id: UUID, case_version: int, task_id: UUID
    ) -> PersistedCreditOpsOutput | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id from public.credit_ops_packages
                where case_id = %s and case_version = %s and task_id = %s
                """,
                (case_id, case_version, task_id),
            )
            package_row = await cursor.fetchone()
            if package_row is None:
                return None
            package_id = cast(UUID, package_row[0])
            handoff_id = await self._find_ready_handoff(
                connection, case_id, case_version, task_id, HUMAN_DECISION_HANDOFF_STATE
            )
        if handoff_id is None:
            return None
        return PersistedCreditOpsOutput(
            package_id=package_id,
            handoff_id=handoff_id,
            handoff_state=HUMAN_DECISION_HANDOFF_STATE,
            created=False,
        )

    async def persist_package(
        self,
        *,
        package: CreditOpsPackage,
        handoff_id: UUID,
        handoff_state: str,
    ) -> PersistedCreditOpsOutput:
        provenance = package.provenance
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.credit_ops_packages (
                      id, case_id, case_version, task_id, execution_id,
                      prompt_version, model_id, endpoint_id, package,
                      package_schema_version, evidence_view_built_at
                    ) values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      'credit-ops-package-v1', %s
                    )
                    on conflict (case_id, case_version, task_id) do nothing
                    returning id
                    """,
                    (
                        package.id,
                        provenance.case_id,
                        provenance.case_version,
                        provenance.task_id,
                        provenance.execution_id,
                        provenance.prompt_version,
                        provenance.model_id,
                        provenance.endpoint_id,
                        Jsonb(package.model_dump(mode="json")),
                        provenance.evidence_view_built_at,
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is None:
                    cursor = await connection.execute(
                        """
                        select id from public.credit_ops_packages
                        where case_id = %s and case_version = %s and task_id = %s
                        """,
                        (provenance.case_id, provenance.case_version, provenance.task_id),
                    )
                    existing = await cursor.fetchone()
                    if existing is None:
                        raise RuntimeError("package idempotency row disappeared")
                    existing_id = cast(UUID, existing[0])
                    existing_handoff = await self._find_ready_handoff(
                        connection,
                        provenance.case_id,
                        provenance.case_version,
                        provenance.task_id,
                        handoff_state,
                    )
                    if existing_handoff is None:
                        raise RuntimeError("persisted package has no handoff")
                    return PersistedCreditOpsOutput(
                        package_id=existing_id,
                        handoff_id=existing_handoff,
                        handoff_state=handoff_state,
                        created=False,
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
                                "packageId": str(package.id),
                                "executionId": str(provenance.execution_id),
                                "schemaVersion": "credit-ops-package-v1",
                            }
                        ),
                        _ACTOR_TYPE,
                    ),
                )
        return PersistedCreditOpsOutput(
            package_id=package.id,
            handoff_id=handoff_id,
            handoff_state=handoff_state,
            created=True,
        )

    async def record_action_authorization(
        self,
        *,
        authorization_id: UUID,
        package_id: UUID,
        action_id: UUID,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> ActionAuthorizationRecord:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                case_id, case_version = await self._package_case_scope(connection, package_id)
                cursor = await connection.execute(
                    """
                    insert into public.ops_action_authorizations (
                      id, package_id, case_id, case_version, action_id,
                      actor_id, actor_role, rationale_vi
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        authorization_id,
                        package_id,
                        case_id,
                        case_version,
                        action_id,
                        actor_id,
                        actor_role,
                        rationale_vi,
                    ),
                )
                created = await cursor.fetchone()
        created_at = cast(datetime, created[0]) if created is not None else datetime.now(UTC)
        return ActionAuthorizationRecord(
            id=authorization_id,
            package_id=package_id,
            action_id=action_id,
            actor_id=actor_id,
            actor_role=actor_role,
            rationale_vi=rationale_vi,
            created_at=created_at,
        )

    async def record_document_request_approval(
        self,
        *,
        approval_id: UUID,
        package_id: UUID,
        request_id: UUID,
        actor_id: UUID,
        actor_role: str,
        rationale_vi: str,
    ) -> DocumentRequestApprovalRecord:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                case_id, case_version = await self._package_case_scope(connection, package_id)
                cursor = await connection.execute(
                    """
                    insert into public.document_request_approvals (
                      id, package_id, case_id, case_version, request_id,
                      actor_id, actor_role, rationale_vi
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        approval_id,
                        package_id,
                        case_id,
                        case_version,
                        request_id,
                        actor_id,
                        actor_role,
                        rationale_vi,
                    ),
                )
                created = await cursor.fetchone()
        created_at = cast(datetime, created[0]) if created is not None else datetime.now(UTC)
        return DocumentRequestApprovalRecord(
            id=approval_id,
            package_id=package_id,
            request_id=request_id,
            actor_id=actor_id,
            actor_role=actor_role,
            rationale_vi=rationale_vi,
            created_at=created_at,
        )

    @staticmethod
    async def _package_case_scope(
        connection: DatabaseConnection, package_id: UUID
    ) -> tuple[UUID, int]:
        cursor = await connection.execute(
            "select case_id, case_version from public.credit_ops_packages where id = %s",
            (package_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError(f"unknown credit-ops package: {package_id}")
        return cast(UUID, row[0]), int(row[1])

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
