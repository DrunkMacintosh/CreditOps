"""Durable Postgres adapter for stage-14 settlement (14A) and recovery (14B).

Every write is human-only and case/version scoped.  Each ``record_*`` method is
ONE transaction that writes the domain row(s) and a ``HUMAN:<role>`` audit event
together -- never a partial write.  ``approve_recovery_strategy`` is a single
select-for-update + update + audit transaction that re-checks the PREPARING
precondition inside the transaction (defence in depth over the database trigger
and the application pre-check), so a lost race can never approve twice.  Nothing
here confirms a gate or drives orchestration.

All identifiers and data are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.settlement_recovery import (
    RecordedRecoveryCase,
    RecordedSettlementCheck,
    RecordedSettlementReceipt,
    RecoveryCaseNotFound,
    RecoveryStrategyConflict,
)
from creditops.domain.settlement_recovery import (
    RecoveryCase,
    RecoveryOption,
    RecoveryStatus,
    SettlementCheck,
    SettlementReceiptKind,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_SETTLEMENT_CHECK_ARTIFACT = "SETTLEMENT_CHECK"
_SETTLEMENT_RECEIPT_ARTIFACT = "SETTLEMENT_RECEIPT"
_RECOVERY_CASE_ARTIFACT = "RECOVERY_CASE"
_CHECK_RECORDED_EVENT = "SETTLEMENT_CHECK_RECORDED"
_RECEIPTS_RECORDED_EVENT = "SETTLEMENT_RECEIPTS_RECORDED"
_RECOVERY_OPENED_EVENT = "RECOVERY_CASE_OPENED"
_RECOVERY_APPROVED_EVENT = "RECOVERY_STRATEGY_APPROVED"
#: ``append_audit`` is used only by the confirm / approve gate surfaces, both
#: independent human OPS-checker acts, so the audit actor type is fixed.
_CONFIRM_ACTOR_TYPE = "HUMAN:OPS_CHECKER"


class PostgresSettlementRecoveryRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- settlement (14A) -----------------------------------------------------

    async def record_settlement_check(
        self, *, check: SettlementCheck, actor_id: UUID, actor_role: str
    ) -> RecordedSettlementCheck:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.settlement_checks (
                      id, case_id, case_version, outstanding_principal,
                      outstanding_interest, outstanding_fees, open_exception_count,
                      zero_balance_confirmed, recorded_by
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        check.id,
                        check.case_id,
                        check.case_version,
                        check.outstanding_principal,
                        check.outstanding_interest,
                        check.outstanding_fees,
                        check.open_exception_count,
                        check.zero_balance_confirmed,
                        check.recorded_by,
                    ),
                )
                inserted = await cursor.fetchone()
                created_at = cast(datetime, inserted[0]) if inserted is not None else None
                await self._insert_audit(
                    connection,
                    case_id=check.case_id,
                    case_version=check.case_version,
                    event_type=_CHECK_RECORDED_EVENT,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_type=_SETTLEMENT_CHECK_ARTIFACT,
                    artifact_id=check.id,
                    event_data={
                        "settlementCheckId": str(check.id),
                        "zeroBalanceConfirmed": check.zero_balance_confirmed,
                        "openExceptionCount": check.open_exception_count,
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return RecordedSettlementCheck(
            id=check.id,
            case_id=check.case_id,
            case_version=check.case_version,
            outstanding_principal=check.outstanding_principal,
            outstanding_interest=check.outstanding_interest,
            outstanding_fees=check.outstanding_fees,
            open_exception_count=check.open_exception_count,
            zero_balance_confirmed=check.zero_balance_confirmed,
            recorded_by=check.recorded_by,
            created_at=cast(datetime, created_at),
        )

    async def list_settlement_checks(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedSettlementCheck, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, outstanding_principal,
                       outstanding_interest, outstanding_fees, open_exception_count,
                       zero_balance_confirmed, recorded_by, created_at
                from public.settlement_checks
                where case_id = %s and case_version = %s
                order by created_at desc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_check(row) for row in rows)

    async def load_latest_settlement_check(
        self, case_id: UUID, case_version: int
    ) -> RecordedSettlementCheck | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, outstanding_principal,
                       outstanding_interest, outstanding_fees, open_exception_count,
                       zero_balance_confirmed, recorded_by, created_at
                from public.settlement_checks
                where case_id = %s and case_version = %s
                order by created_at desc, id asc
                limit 1
                """,
                (case_id, case_version),
            )
            row = await cursor.fetchone()
        return _row_to_check(row) if row is not None else None

    async def record_settlement_receipts(
        self,
        *,
        settlement_check_id: UUID,
        case_id: UUID,
        case_version: int,
        receipts: Sequence[tuple[SettlementReceiptKind, str | None]],
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedSettlementReceipt, ...]:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                for kind, note in receipts:
                    # Idempotent: a receipt kind already present is left as-is.
                    await connection.execute(
                        """
                        insert into public.settlement_receipts (
                          settlement_check_id, kind, note_vi, recorded_by
                        ) values (%s, %s, %s, %s)
                        on conflict (settlement_check_id, kind) do nothing
                        """,
                        (settlement_check_id, kind.value, note, actor_id),
                    )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_RECEIPTS_RECORDED_EVENT,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_type=_SETTLEMENT_RECEIPT_ARTIFACT,
                    artifact_id=settlement_check_id,
                    event_data={
                        "settlementCheckId": str(settlement_check_id),
                        "kinds": [kind.value for kind, _ in receipts],
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
                cursor = await connection.execute(
                    """
                    select id, settlement_check_id, kind, note_vi, recorded_by,
                           created_at
                    from public.settlement_receipts
                    where settlement_check_id = %s
                    order by created_at asc, id asc
                    """,
                    (settlement_check_id,),
                )
                rows = await cursor.fetchall()
        return tuple(_row_to_receipt(row) for row in rows)

    async def list_settlement_receipts(
        self, settlement_check_id: UUID
    ) -> tuple[RecordedSettlementReceipt, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, settlement_check_id, kind, note_vi, recorded_by,
                       created_at
                from public.settlement_receipts
                where settlement_check_id = %s
                order by created_at asc, id asc
                """,
                (settlement_check_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_receipt(row) for row in rows)

    # -- recovery (14B) -------------------------------------------------------

    async def record_recovery_case(
        self, *, recovery: RecoveryCase, actor_id: UUID, actor_role: str
    ) -> RecordedRecoveryCase:
        options_payload = [option.model_dump(mode="json") for option in recovery.options]
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.recovery_cases (
                      id, case_id, case_version, trigger_summary_vi, escalated_by,
                      escalation_rationale_vi, status, evidence_refs, options
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        recovery.id,
                        recovery.case_id,
                        recovery.case_version,
                        recovery.trigger_summary_vi,
                        recovery.escalated_by,
                        recovery.escalation_rationale_vi,
                        recovery.status.value,
                        Jsonb(list(recovery.evidence_refs)),
                        Jsonb(options_payload),
                    ),
                )
                inserted = await cursor.fetchone()
                created_at = cast(datetime, inserted[0]) if inserted is not None else None
                await self._insert_audit(
                    connection,
                    case_id=recovery.case_id,
                    case_version=recovery.case_version,
                    event_type=_RECOVERY_OPENED_EVENT,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    artifact_type=_RECOVERY_CASE_ARTIFACT,
                    artifact_id=recovery.id,
                    event_data={
                        "recoveryCaseId": str(recovery.id),
                        "escalatedBy": str(recovery.escalated_by),
                        "optionCount": len(recovery.options),
                        "evidenceRefCount": len(recovery.evidence_refs),
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return RecordedRecoveryCase(
            id=recovery.id,
            case_id=recovery.case_id,
            case_version=recovery.case_version,
            trigger_summary_vi=recovery.trigger_summary_vi,
            escalated_by=recovery.escalated_by,
            escalation_rationale_vi=recovery.escalation_rationale_vi,
            status=recovery.status,
            evidence_refs=recovery.evidence_refs,
            options=recovery.options,
            approved_by=None,
            created_at=cast(datetime, created_at),
        )

    async def list_recovery_cases(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedRecoveryCase, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, trigger_summary_vi, escalated_by,
                       escalation_rationale_vi, status, evidence_refs, options,
                       approved_by, created_at
                from public.recovery_cases
                where case_id = %s and case_version = %s
                order by created_at desc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_recovery(row) for row in rows)

    async def load_recovery_case(
        self, recovery_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedRecoveryCase | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, trigger_summary_vi, escalated_by,
                       escalation_rationale_vi, status, evidence_refs, options,
                       approved_by, created_at
                from public.recovery_cases
                where id = %s and case_id = %s and case_version = %s
                """,
                (recovery_id, case_id, case_version),
            )
            row = await cursor.fetchone()
        return _row_to_recovery(row) if row is not None else None

    async def approve_recovery_strategy(
        self,
        *,
        recovery_id: UUID,
        case_id: UUID,
        case_version: int,
        approved_by: UUID,
        actor_role: str,
    ) -> RecordedRecoveryCase:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    select id, case_id, case_version, trigger_summary_vi,
                           escalated_by, escalation_rationale_vi, status,
                           evidence_refs, options, approved_by, created_at
                    from public.recovery_cases
                    where id = %s and case_id = %s and case_version = %s
                    for update
                    """,
                    (recovery_id, case_id, case_version),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RecoveryCaseNotFound(str(recovery_id))
                current = _row_to_recovery(row)
                if current.status is not RecoveryStatus.PREPARING:
                    # Fail-closed backstop for a lost race / double approval.
                    raise RecoveryStrategyConflict(current.status.value)
                await connection.execute(
                    """
                    update public.recovery_cases
                    set status = 'STRATEGY_APPROVED',
                        approved_by = %s,
                        strategy_approved_at = clock_timestamp()
                    where id = %s and case_id = %s and case_version = %s
                    """,
                    (approved_by, recovery_id, case_id, case_version),
                )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type=_RECOVERY_APPROVED_EVENT,
                    actor_id=approved_by,
                    actor_role=actor_role,
                    artifact_type=_RECOVERY_CASE_ARTIFACT,
                    artifact_id=recovery_id,
                    event_data={
                        "recoveryCaseId": str(recovery_id),
                        "approvedBy": str(approved_by),
                        "escalatedBy": str(current.escalated_by),
                        "actorRole": actor_role,
                    },
                )
        return RecordedRecoveryCase(
            id=current.id,
            case_id=current.case_id,
            case_version=current.case_version,
            trigger_summary_vi=current.trigger_summary_vi,
            escalated_by=current.escalated_by,
            escalation_rationale_vi=current.escalation_rationale_vi,
            status=RecoveryStatus.STRATEGY_APPROVED,
            evidence_refs=current.evidence_refs,
            options=current.options,
            approved_by=approved_by,
            created_at=current.created_at,
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
                        _CONFIRM_ACTOR_TYPE,
                        event.artifact_type,
                        event.artifact_id,
                        Jsonb(event_data),
                    ),
                )

    # -- helpers --------------------------------------------------------------

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


def _row_to_check(row: Sequence[Any]) -> RecordedSettlementCheck:
    return RecordedSettlementCheck(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        outstanding_principal=str(row[3]),
        outstanding_interest=str(row[4]),
        outstanding_fees=str(row[5]),
        open_exception_count=int(cast(int, row[6])),
        zero_balance_confirmed=bool(row[7]),
        recorded_by=cast(UUID, row[8]),
        created_at=cast(datetime, row[9]),
    )


def _row_to_receipt(row: Sequence[Any]) -> RecordedSettlementReceipt:
    return RecordedSettlementReceipt(
        id=cast(UUID, row[0]),
        settlement_check_id=cast(UUID, row[1]),
        kind=SettlementReceiptKind(str(row[2])),
        note_vi=cast("str | None", row[3]),
        recorded_by=cast(UUID, row[4]),
        created_at=cast(datetime, row[5]),
    )


def _row_to_recovery(row: Sequence[Any]) -> RecordedRecoveryCase:
    options = tuple(
        RecoveryOption.model_validate(option) for option in (row[8] or [])
    )
    return RecordedRecoveryCase(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        trigger_summary_vi=str(row[3]),
        escalated_by=cast(UUID, row[4]),
        escalation_rationale_vi=str(row[5]),
        status=RecoveryStatus(str(row[6])),
        evidence_refs=tuple(str(ref) for ref in (row[7] or [])),
        options=options,
        approved_by=cast("UUID | None", row[9]),
        created_at=cast(datetime, row[10]),
    )
