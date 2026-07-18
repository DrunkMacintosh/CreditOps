"""Durable Postgres adapter for the stage-12 post-credit monitoring surfaces.

Every write is human-driven and case/version scoped.  ``record_observation`` and
``record_covenant_test`` each run as ONE transaction that writes the append-only
domain row, the ``OPEN`` early-warning alert raised by the deterministic rule (if
any), and a ``HUMAN:<role>`` audit event together -- never a partial write; the
alert insert is deduped (``on conflict do nothing``) so a rule can never raise a
second alert for the same source.  ``dispose_alert`` re-checks the domain alert
lifecycle inside the transaction (defence in depth over the database trigger and
the application pre-check).  Nothing here confirms a gate or drives orchestration,
and there is NO debt classification anywhere.

All identifiers and data are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.monitoring import (
    AlertNotFound,
    ForbiddenAlertTransition,
    RecordedAlert,
    RecordedCovenant,
    RecordedCovenantTest,
    RecordedObligation,
    RecordedObservation,
)
from creditops.domain.monitoring import (
    AlertRule,
    AlertStatus,
    ComparisonOperator,
    Covenant,
    CovenantEvaluation,
    EarlyWarningAlert,
    GeneratedObligation,
    MonitoringObservation,
    ObligationFrequency,
    ObligationSpec,
    is_alert_transition_allowed,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

_OBLIGATION_ARTIFACT = "MONITORING_OBLIGATION"
_OBSERVATION_ARTIFACT = "MONITORING_OBSERVATION"
_COVENANT_ARTIFACT = "COVENANT"
_COVENANT_TEST_ARTIFACT = "COVENANT_TEST"
_ALERT_ARTIFACT = "EARLY_WARNING_ALERT"
#: A rule-raised alert has NO human actor: it is written by a deterministic rule
#: inside the human action's transaction, audited with this system actor type and
#: a null actor_id.
_RULE_ACTOR_TYPE = "SYSTEM:DETERMINISTIC_RULE"


class PostgresMonitoringRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    # -- obligations ----------------------------------------------------------

    async def create_obligations(
        self,
        *,
        case_id: UUID,
        case_version: int,
        spec: ObligationSpec,
        obligations: tuple[GeneratedObligation, ...],
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedObligation, ...]:
        recorded: list[RecordedObligation] = []
        async with self._connection_factory() as connection:
            async with connection.transaction():
                for obligation in obligations:
                    cursor = await connection.execute(
                        """
                        insert into public.monitoring_obligations (
                          case_id, case_version, sequence, frequency, due_date,
                          requirement_text_vi
                        ) values (%s, %s, %s, %s, %s, %s)
                        returning id, created_at
                        """,
                        (
                            case_id,
                            case_version,
                            obligation.sequence,
                            obligation.frequency.value,
                            obligation.due_date,
                            obligation.requirement_text_vi,
                        ),
                    )
                    row = await cursor.fetchone()
                    obligation_id = cast(UUID, row[0]) if row is not None else None
                    created_at = cast(datetime, row[1]) if row is not None else None
                    recorded.append(
                        RecordedObligation(
                            id=cast(UUID, obligation_id),
                            case_id=case_id,
                            case_version=case_version,
                            sequence=obligation.sequence,
                            frequency=obligation.frequency,
                            due_date=obligation.due_date,
                            requirement_text_vi=obligation.requirement_text_vi,
                            created_at=cast(datetime, created_at),
                        )
                    )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type="MONITORING_OBLIGATIONS_GENERATED",
                    actor_type=f"HUMAN:{actor_role}",
                    actor_id=actor_id,
                    artifact_type=_OBLIGATION_ARTIFACT,
                    artifact_id=case_id,
                    event_data={
                        "frequency": spec.frequency.value,
                        "count": len(obligations),
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return tuple(recorded)

    async def list_obligations(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedObligation, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, sequence, frequency, due_date,
                       requirement_text_vi, created_at
                from public.monitoring_obligations
                where case_id = %s and case_version = %s
                order by sequence asc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_obligation(row) for row in rows)

    async def load_obligation(
        self, obligation_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedObligation | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, sequence, frequency, due_date,
                       requirement_text_vi, created_at
                from public.monitoring_obligations
                where id = %s and case_id = %s and case_version = %s
                """,
                (obligation_id, case_id, case_version),
            )
            row = await cursor.fetchone()
        return _row_to_obligation(row) if row is not None else None

    # -- observations ---------------------------------------------------------

    async def record_observation(
        self,
        *,
        observation: MonitoringObservation,
        overdue_alert: EarlyWarningAlert | None,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedObservation, RecordedAlert | None]:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.monitoring_observations (
                      id, case_id, case_version, obligation_id,
                      observation_type_vi, body_vi, effective_at, observed_at,
                      evidence_refs, recorded_by, recorded_by_role
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning recorded_at
                    """,
                    (
                        observation.id,
                        observation.case_id,
                        observation.case_version,
                        observation.obligation_id,
                        observation.observation_type_vi,
                        observation.body_vi,
                        observation.effective_at,
                        observation.observed_at,
                        Jsonb(list(observation.evidence_refs)),
                        actor_id,
                        actor_role,
                    ),
                )
                inserted = await cursor.fetchone()
                recorded_at = (
                    cast(datetime, inserted[0]) if inserted is not None else None
                )
                await self._insert_audit(
                    connection,
                    case_id=observation.case_id,
                    case_version=observation.case_version,
                    event_type="MONITORING_OBSERVATION_RECORDED",
                    actor_type=f"HUMAN:{actor_role}",
                    actor_id=actor_id,
                    artifact_type=_OBSERVATION_ARTIFACT,
                    artifact_id=observation.id,
                    event_data={
                        "observationId": str(observation.id),
                        "obligationId": (
                            str(observation.obligation_id)
                            if observation.obligation_id is not None
                            else None
                        ),
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
                recorded_alert = await self._raise_overdue_alert(
                    connection, overdue_alert
                )
        return (
            RecordedObservation(
                id=observation.id,
                case_id=observation.case_id,
                case_version=observation.case_version,
                obligation_id=observation.obligation_id,
                observation_type_vi=observation.observation_type_vi,
                body_vi=observation.body_vi,
                effective_at=observation.effective_at,
                observed_at=observation.observed_at,
                recorded_at=cast(datetime, recorded_at),
                evidence_refs=observation.evidence_refs,
            ),
            recorded_alert,
        )

    async def list_observations(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedObservation, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, obligation_id,
                       observation_type_vi, body_vi, effective_at, observed_at,
                       recorded_at, evidence_refs
                from public.monitoring_observations
                where case_id = %s and case_version = %s
                order by recorded_at asc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_observation(row) for row in rows)

    # -- covenants ------------------------------------------------------------

    async def create_covenant(
        self, *, covenant: Covenant, actor_id: UUID, actor_role: str
    ) -> RecordedCovenant:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.covenants (
                      id, case_id, case_version, name_vi, metric_key, operator,
                      threshold_value, threshold_version, created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning created_at
                    """,
                    (
                        covenant.id,
                        covenant.case_id,
                        covenant.case_version,
                        covenant.name_vi,
                        covenant.threshold.metric_key,
                        covenant.threshold.operator.value,
                        covenant.threshold.threshold_value,
                        covenant.threshold.threshold_version,
                        actor_id,
                    ),
                )
                inserted = await cursor.fetchone()
                created_at = (
                    cast(datetime, inserted[0]) if inserted is not None else None
                )
                await self._insert_audit(
                    connection,
                    case_id=covenant.case_id,
                    case_version=covenant.case_version,
                    event_type="COVENANT_CREATED",
                    actor_type=f"HUMAN:{actor_role}",
                    actor_id=actor_id,
                    artifact_type=_COVENANT_ARTIFACT,
                    artifact_id=covenant.id,
                    event_data={
                        "covenantId": str(covenant.id),
                        "metricKey": covenant.threshold.metric_key,
                        "operator": covenant.threshold.operator.value,
                        "thresholdVersion": covenant.threshold.threshold_version,
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return RecordedCovenant(
            id=covenant.id,
            case_id=covenant.case_id,
            case_version=covenant.case_version,
            name_vi=covenant.name_vi,
            metric_key=covenant.threshold.metric_key,
            operator=covenant.threshold.operator,
            threshold_value=covenant.threshold.threshold_value,
            threshold_version=covenant.threshold.threshold_version,
            created_at=cast(datetime, created_at),
        )

    async def list_covenants(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCovenant, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, name_vi, metric_key, operator,
                       threshold_value, threshold_version, created_at
                from public.covenants
                where case_id = %s and case_version = %s
                order by created_at asc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_covenant(row) for row in rows)

    async def load_covenant(
        self, covenant_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedCovenant | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, name_vi, metric_key, operator,
                       threshold_value, threshold_version, created_at
                from public.covenants
                where id = %s and case_id = %s and case_version = %s
                """,
                (covenant_id, case_id, case_version),
            )
            row = await cursor.fetchone()
        return _row_to_covenant(row) if row is not None else None

    async def record_covenant_test(
        self,
        *,
        test_id: UUID,
        covenant_id: UUID,
        case_id: UUID,
        case_version: int,
        evaluation: CovenantEvaluation,
        breach_alert: EarlyWarningAlert | None,
        actor_id: UUID,
        actor_role: str,
    ) -> tuple[RecordedCovenantTest, RecordedAlert | None]:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.covenant_tests (
                      id, covenant_id, case_id, case_version, metric_key, operator,
                      numerator, denominator, threshold_value, threshold_version,
                      comparison_lhs, comparison_rhs, passed, tested_by,
                      tested_by_role
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning recorded_at
                    """,
                    (
                        test_id,
                        covenant_id,
                        case_id,
                        case_version,
                        evaluation.metric_key,
                        evaluation.operator.value,
                        evaluation.numerator,
                        evaluation.denominator,
                        evaluation.threshold_value,
                        evaluation.threshold_version,
                        evaluation.comparison_lhs,
                        evaluation.comparison_rhs,
                        evaluation.passed,
                        actor_id,
                        actor_role,
                    ),
                )
                inserted = await cursor.fetchone()
                recorded_at = (
                    cast(datetime, inserted[0]) if inserted is not None else None
                )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type="COVENANT_TESTED",
                    actor_type=f"HUMAN:{actor_role}",
                    actor_id=actor_id,
                    artifact_type=_COVENANT_TEST_ARTIFACT,
                    artifact_id=test_id,
                    event_data={
                        "covenantTestId": str(test_id),
                        "covenantId": str(covenant_id),
                        "passed": evaluation.passed,
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
                recorded_alert = await self._raise_breach_alert(connection, breach_alert)
        return (
            RecordedCovenantTest(
                id=test_id,
                covenant_id=covenant_id,
                case_id=case_id,
                case_version=case_version,
                metric_key=evaluation.metric_key,
                operator=evaluation.operator,
                numerator=evaluation.numerator,
                denominator=evaluation.denominator,
                threshold_value=evaluation.threshold_value,
                threshold_version=evaluation.threshold_version,
                comparison_lhs=evaluation.comparison_lhs,
                comparison_rhs=evaluation.comparison_rhs,
                passed=evaluation.passed,
                recorded_at=cast(datetime, recorded_at),
            ),
            recorded_alert,
        )

    async def list_covenant_tests(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedCovenantTest, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, covenant_id, case_id, case_version, metric_key, operator,
                       numerator, denominator, threshold_value, threshold_version,
                       comparison_lhs, comparison_rhs, passed, recorded_at
                from public.covenant_tests
                where case_id = %s and case_version = %s
                order by recorded_at asc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_covenant_test(row) for row in rows)

    # -- alerts ---------------------------------------------------------------

    async def list_alerts(
        self, case_id: UUID, case_version: int
    ) -> tuple[RecordedAlert, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, rule, status, detail_vi,
                       source_covenant_test_id, source_obligation_id,
                       source_observation_id, created_at
                from public.early_warning_alerts
                where case_id = %s and case_version = %s
                order by created_at asc, id asc
                """,
                (case_id, case_version),
            )
            rows = await cursor.fetchall()
        return tuple(_row_to_alert(row) for row in rows)

    async def load_alert(
        self, alert_id: UUID, case_id: UUID, case_version: int
    ) -> RecordedAlert | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, case_id, case_version, rule, status, detail_vi,
                       source_covenant_test_id, source_obligation_id,
                       source_observation_id, created_at
                from public.early_warning_alerts
                where id = %s and case_id = %s and case_version = %s
                """,
                (alert_id, case_id, case_version),
            )
            row = await cursor.fetchone()
        return _row_to_alert(row) if row is not None else None

    async def dispose_alert(
        self,
        *,
        alert_id: UUID,
        case_id: UUID,
        case_version: int,
        to_status: AlertStatus,
        rationale_vi: str,
        actor_id: UUID,
        actor_role: str,
    ) -> RecordedAlert:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    select id, case_id, case_version, rule, status, detail_vi,
                           source_covenant_test_id, source_obligation_id,
                           source_observation_id, created_at
                    from public.early_warning_alerts
                    where id = %s and case_id = %s and case_version = %s
                    for update
                    """,
                    (alert_id, case_id, case_version),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise AlertNotFound(str(alert_id))
                current = _row_to_alert(row)
                if not is_alert_transition_allowed(current.status, to_status):
                    # Fail-closed backstop for a lost race: nothing is written.
                    raise ForbiddenAlertTransition(
                        f"{current.status.value} -> {to_status.value}"
                    )
                await connection.execute(
                    """
                    update public.early_warning_alerts
                    set status = %s
                    where id = %s and case_id = %s and case_version = %s
                    """,
                    (to_status.value, alert_id, case_id, case_version),
                )
                await connection.execute(
                    """
                    insert into public.alert_dispositions (
                      alert_id, from_status, to_status, rationale_vi, actor_id,
                      actor_role
                    ) values (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        alert_id,
                        current.status.value,
                        to_status.value,
                        rationale_vi,
                        actor_id,
                        actor_role,
                    ),
                )
                await self._insert_audit(
                    connection,
                    case_id=case_id,
                    case_version=case_version,
                    event_type="EARLY_WARNING_ALERT_DISPOSED",
                    actor_type=f"HUMAN:{actor_role}",
                    actor_id=actor_id,
                    artifact_type=_ALERT_ARTIFACT,
                    artifact_id=alert_id,
                    event_data={
                        "alertId": str(alert_id),
                        "fromStatus": current.status.value,
                        "toStatus": to_status.value,
                        "actorId": str(actor_id),
                        "actorRole": actor_role,
                    },
                )
        return RecordedAlert(
            id=current.id,
            case_id=current.case_id,
            case_version=current.case_version,
            rule=current.rule,
            status=to_status,
            detail_vi=current.detail_vi,
            source_covenant_test_id=current.source_covenant_test_id,
            source_obligation_id=current.source_obligation_id,
            source_observation_id=current.source_observation_id,
            created_at=current.created_at,
        )

    # -- deterministic rule inserts (dedup, same transaction) -----------------

    async def _raise_overdue_alert(
        self, connection: DatabaseConnection, alert: EarlyWarningAlert | None
    ) -> RecordedAlert | None:
        if alert is None:
            return None
        cursor = await connection.execute(
            """
            insert into public.early_warning_alerts (
              id, case_id, case_version, rule, status, detail_vi,
              source_obligation_id, source_observation_id
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (source_obligation_id) where rule = 'OVERDUE_OBLIGATION'
            do nothing
            returning created_at
            """,
            (
                alert.id,
                alert.case_id,
                alert.case_version,
                alert.rule.value,
                alert.status.value,
                alert.detail_vi,
                alert.source_obligation_id,
                alert.source_observation_id,
            ),
        )
        return await self._finish_rule_alert(connection, cursor, alert)

    async def _raise_breach_alert(
        self, connection: DatabaseConnection, alert: EarlyWarningAlert | None
    ) -> RecordedAlert | None:
        if alert is None:
            return None
        cursor = await connection.execute(
            """
            insert into public.early_warning_alerts (
              id, case_id, case_version, rule, status, detail_vi,
              source_covenant_test_id
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (source_covenant_test_id) where rule = 'COVENANT_BREACH'
            do nothing
            returning created_at
            """,
            (
                alert.id,
                alert.case_id,
                alert.case_version,
                alert.rule.value,
                alert.status.value,
                alert.detail_vi,
                alert.source_covenant_test_id,
            ),
        )
        return await self._finish_rule_alert(connection, cursor, alert)

    async def _finish_rule_alert(
        self, connection: DatabaseConnection, cursor: Any, alert: EarlyWarningAlert
    ) -> RecordedAlert | None:
        inserted = await cursor.fetchone()
        if inserted is None:
            # Dedup: an alert for this source already exists -- nothing raised.
            return None
        created_at = cast(datetime, inserted[0])
        await self._insert_audit(
            connection,
            case_id=alert.case_id,
            case_version=alert.case_version,
            event_type="EARLY_WARNING_ALERT_RAISED",
            actor_type=_RULE_ACTOR_TYPE,
            actor_id=None,
            artifact_type=_ALERT_ARTIFACT,
            artifact_id=alert.id,
            event_data={
                "alertId": str(alert.id),
                "rule": alert.rule.value,
            },
        )
        return RecordedAlert(
            id=alert.id,
            case_id=alert.case_id,
            case_version=alert.case_version,
            rule=alert.rule,
            status=alert.status,
            detail_vi=alert.detail_vi,
            source_covenant_test_id=alert.source_covenant_test_id,
            source_obligation_id=alert.source_obligation_id,
            source_observation_id=alert.source_observation_id,
            created_at=created_at,
        )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    async def _insert_audit(
        connection: DatabaseConnection,
        *,
        case_id: UUID,
        case_version: int,
        event_type: str,
        actor_type: str,
        actor_id: UUID | None,
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
                actor_type,
                actor_id,
                artifact_type,
                artifact_id,
                Jsonb(event_data),
            ),
        )


def _row_to_obligation(row: Sequence[Any]) -> RecordedObligation:
    return RecordedObligation(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        sequence=int(cast(int, row[3])),
        frequency=ObligationFrequency(str(row[4])),
        due_date=cast(date, row[5]),
        requirement_text_vi=str(row[6]),
        created_at=cast(datetime, row[7]),
    )


def _row_to_observation(row: Sequence[Any]) -> RecordedObservation:
    return RecordedObservation(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        obligation_id=cast("UUID | None", row[3]),
        observation_type_vi=str(row[4]),
        body_vi=str(row[5]),
        effective_at=cast(datetime, row[6]),
        observed_at=cast(datetime, row[7]),
        recorded_at=cast(datetime, row[8]),
        evidence_refs=tuple(str(ref) for ref in (row[9] or [])),
    )


def _row_to_covenant(row: Sequence[Any]) -> RecordedCovenant:
    return RecordedCovenant(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        name_vi=str(row[3]),
        metric_key=str(row[4]),
        operator=ComparisonOperator(str(row[5])),
        threshold_value=cast(Decimal, row[6]),
        threshold_version=int(cast(int, row[7])),
        created_at=cast(datetime, row[8]),
    )


def _row_to_covenant_test(row: Sequence[Any]) -> RecordedCovenantTest:
    return RecordedCovenantTest(
        id=cast(UUID, row[0]),
        covenant_id=cast(UUID, row[1]),
        case_id=cast(UUID, row[2]),
        case_version=int(cast(int, row[3])),
        metric_key=str(row[4]),
        operator=ComparisonOperator(str(row[5])),
        numerator=cast(Decimal, row[6]),
        denominator=cast(Decimal, row[7]),
        threshold_value=cast(Decimal, row[8]),
        threshold_version=int(cast(int, row[9])),
        comparison_lhs=cast(Decimal, row[10]),
        comparison_rhs=cast(Decimal, row[11]),
        passed=bool(row[12]),
        recorded_at=cast(datetime, row[13]),
    )


def _row_to_alert(row: Sequence[Any]) -> RecordedAlert:
    return RecordedAlert(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        rule=AlertRule(str(row[3])),
        status=AlertStatus(str(row[4])),
        detail_vi=str(row[5]),
        source_covenant_test_id=cast("UUID | None", row[6]),
        source_obligation_id=cast("UUID | None", row[7]),
        source_observation_id=cast("UUID | None", row[8]),
        created_at=cast(datetime, row[9]),
    )
