"""Durable Postgres adapter for the versioned FinancingRequest (stage 2).

Every write is bounded and append-only.  ``append_version`` never updates a prior
version: it computes the next ``request_version`` (``max + 1``) and inserts a NEW
row, retrying on the ``(case_id, request_version)`` unique conflict a bounded
number of times so a concurrent append lands at the following version rather than
clobbering anyone.  Reads are always scoped by ``case_id``.  Monetary columns are
projected ``::text`` so exact whole-currency decimals never pass through a float.

Nothing here can satisfy a gate, record a decision, resolve a gap, or confirm a
fact.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.domain.financing_requests import (
    FinancingRequestDraft,
    FinancingRequestVersion,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory

#: Human intake officer is the only writer of a financing-request version.
_ACTOR_TYPE = "HUMAN:INTAKE_OFFICER"

#: A concurrent append can lose the (case, version) race; retry a bounded number
#: of times so it simply lands at the next version rather than failing the human.
_MAX_APPEND_ATTEMPTS = 8

_SELECT_COLUMNS = """
  id, case_id, case_version, request_version, requested_amount::text, purpose_vi,
  currency, product_vi, term_months, expected_use_date, repayment_source_vi,
  repayment_plan_vi, proposed_security_vi, customer_own_funds::text,
  connected_trade_products_vi, working_capital_cycle_vi,
  key_suppliers_customers_vi, proposed_cash_flow_controls_vi, created_by,
  created_at
"""


def _from_row(row: tuple[object, ...]) -> FinancingRequestVersion:
    return FinancingRequestVersion(
        id=cast(UUID, row[0]),
        case_id=cast(UUID, row[1]),
        case_version=int(cast(int, row[2])),
        request_version=int(cast(int, row[3])),
        requested_amount=str(row[4]),
        purpose_vi=str(row[5]),
        currency=cast("str | None", row[6]),
        product_vi=cast("str | None", row[7]),
        term_months=cast("int | None", row[8]),
        expected_use_date=cast("date | None", row[9]),
        repayment_source_vi=cast("str | None", row[10]),
        repayment_plan_vi=cast("str | None", row[11]),
        proposed_security_vi=cast("str | None", row[12]),
        customer_own_funds=None if row[13] is None else str(row[13]),
        connected_trade_products_vi=cast("str | None", row[14]),
        working_capital_cycle_vi=cast("str | None", row[15]),
        key_suppliers_customers_vi=cast("str | None", row[16]),
        proposed_cash_flow_controls_vi=cast("str | None", row[17]),
        created_by=cast(UUID, row[18]),
        created_at=cast(datetime, row[19]),
    )


class PostgresFinancingRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def list_versions(self, case_id: UUID) -> tuple[FinancingRequestVersion, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                f"""
                select {_SELECT_COLUMNS}
                from public.financing_requests
                where case_id = %s
                order by request_version
                """,
                (case_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_from_row(tuple(row)) for row in rows)

    async def latest_version(self, case_id: UUID) -> FinancingRequestVersion | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                f"""
                select {_SELECT_COLUMNS}
                from public.financing_requests
                where case_id = %s
                order by request_version desc
                limit 1
                """,
                (case_id,),
            )
            row = await cursor.fetchone()
        return None if row is None else _from_row(tuple(row))

    async def append_version(
        self,
        *,
        case_id: UUID,
        case_version: int,
        fields: FinancingRequestDraft,
        actor_id: UUID,
    ) -> FinancingRequestVersion:
        async with self._connection_factory() as connection:
            for _ in range(_MAX_APPEND_ATTEMPTS):
                async with connection.transaction():
                    cursor = await connection.execute(
                        """
                        select coalesce(max(request_version), 0) + 1
                        from public.financing_requests
                        where case_id = %s
                        """,
                        (case_id,),
                    )
                    next_row = await cursor.fetchone()
                    next_version = int(cast(int, next_row[0])) if next_row else 1
                    new_id = uuid4()
                    cursor = await connection.execute(
                        """
                        insert into public.financing_requests (
                          id, case_id, case_version, request_version,
                          requested_amount, purpose_vi, currency, product_vi,
                          term_months, expected_use_date, repayment_source_vi,
                          repayment_plan_vi, proposed_security_vi,
                          customer_own_funds, connected_trade_products_vi,
                          working_capital_cycle_vi, key_suppliers_customers_vi,
                          proposed_cash_flow_controls_vi, created_by
                        ) values (
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s
                        )
                        on conflict (case_id, request_version) do nothing
                        returning created_at
                        """,
                        (
                            new_id,
                            case_id,
                            case_version,
                            next_version,
                            fields.requested_amount,
                            fields.purpose_vi,
                            fields.currency,
                            fields.product_vi,
                            fields.term_months,
                            fields.expected_use_date,
                            fields.repayment_source_vi,
                            fields.repayment_plan_vi,
                            fields.proposed_security_vi,
                            fields.customer_own_funds,
                            fields.connected_trade_products_vi,
                            fields.working_capital_cycle_vi,
                            fields.key_suppliers_customers_vi,
                            fields.proposed_cash_flow_controls_vi,
                            actor_id,
                        ),
                    )
                    inserted = await cursor.fetchone()
                if inserted is None:
                    # Another append took this version; recompute and retry.
                    continue
                return FinancingRequestVersion(
                    id=new_id,
                    case_id=case_id,
                    case_version=case_version,
                    request_version=next_version,
                    requested_amount=fields.requested_amount,
                    purpose_vi=fields.purpose_vi,
                    currency=fields.currency,
                    product_vi=fields.product_vi,
                    term_months=fields.term_months,
                    expected_use_date=fields.expected_use_date,
                    repayment_source_vi=fields.repayment_source_vi,
                    repayment_plan_vi=fields.repayment_plan_vi,
                    proposed_security_vi=fields.proposed_security_vi,
                    customer_own_funds=fields.customer_own_funds,
                    connected_trade_products_vi=fields.connected_trade_products_vi,
                    working_capital_cycle_vi=fields.working_capital_cycle_vi,
                    key_suppliers_customers_vi=fields.key_suppliers_customers_vi,
                    proposed_cash_flow_controls_vi=fields.proposed_cash_flow_controls_vi,
                    created_by=actor_id,
                    created_at=cast(datetime, inserted[0]),
                )
        raise RuntimeError(
            "could not append a financing-request version after "
            f"{_MAX_APPEND_ATTEMPTS} attempts (persistent version contention)"
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
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.case_id,
                        event.case_version,
                        event.event_type,
                        _ACTOR_TYPE,
                        None,
                        event.artifact_type,
                        event.artifact_id,
                        Jsonb(event_data),
                    ),
                )
