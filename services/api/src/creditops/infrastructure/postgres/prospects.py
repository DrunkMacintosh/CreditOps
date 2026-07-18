"""Durable Postgres adapter for the Stage 1 prospect store.

Every write is bounded and append-only (no UPDATE/DELETE path exists), and
every read is scoped by ``created_by`` -- the owning intake officer -- so the
service-role write path can never surface another officer's prospect.  A
mutation that targets a prospect the actor does not own resolves the ownership
gate to nothing and raises ``ProspectNotFound``, which the API turns into an
indistinguishable 404.  Nothing here computes a verdict or triggers a contact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.prospects import ProspectDetail, ProspectNotFound
from creditops.domain.prospects import (
    ContactDecision,
    ContactDecisionRecord,
    Prospect,
    ScreeningSnapshot,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection


class PostgresProspectRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def create_prospect(
        self,
        *,
        name_vi: str,
        industry_vi: str | None,
        years_operating: int | None,
        revenue_band_vi: str | None,
        legal_status_vi: str | None,
        notes_vi: str | None,
        created_by: UUID,
    ) -> Prospect:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.prospects (
                      name_vi, industry_vi, years_operating, revenue_band_vi,
                      legal_status_vi, notes_vi, created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    returning id, created_at
                    """,
                    (
                        name_vi,
                        industry_vi,
                        years_operating,
                        revenue_band_vi,
                        legal_status_vi,
                        notes_vi,
                        created_by,
                    ),
                )
                row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("prospect insert returned no row")
        return Prospect(
            id=cast(UUID, row[0]),
            name_vi=name_vi,
            industry_vi=industry_vi,
            years_operating=years_operating,
            revenue_band_vi=revenue_band_vi,
            legal_status_vi=legal_status_vi,
            notes_vi=notes_vi,
            created_by=created_by,
            created_at=cast(datetime, row[1]),
        )

    async def list_prospects(
        self, *, created_by: UUID, cursor: UUID | None, limit: int
    ) -> tuple[tuple[Prospect, ...], UUID | None]:
        # Newest-first, keyset-paginated by (created_at, id) descending, scoped
        # to the owner.  The cursor row's own (created_at, id) is resolved via a
        # subselect in this SAME query, scoped to this created_by, so a cursor
        # for another officer's prospect (or an unknown id) simply yields an
        # empty page rather than leaking a cross-officer position.
        position_clause = ""
        params: list[object] = [created_by]
        if cursor is not None:
            position_clause = """
              and (created_at, id) < (
                select created_at, id
                from public.prospects
                where id = %s and created_by = %s
              )
            """
            params.extend([cursor, created_by])
        params.append(limit + 1)

        async with self._connection_factory() as connection:
            result = await connection.execute(
                f"""
                select id, name_vi, industry_vi, years_operating, revenue_band_vi,
                       legal_status_vi, notes_vi, created_by, created_at
                from public.prospects
                where created_by = %s
                {position_clause}
                order by created_at desc, id desc
                limit %s
                """,
                params,
            )
            rows = await result.fetchall()

        records = tuple(_prospect_from_row(row) for row in rows)
        page = records[:limit]
        next_cursor = page[-1].id if len(records) > limit else None
        return page, next_cursor

    async def load_prospect(
        self, *, prospect_id: UUID, created_by: UUID
    ) -> ProspectDetail | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                select id, name_vi, industry_vi, years_operating, revenue_band_vi,
                       legal_status_vi, notes_vi, created_by, created_at
                from public.prospects
                where id = %s and created_by = %s
                """,
                (prospect_id, created_by),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            prospect = _prospect_from_row(row)

            cursor = await connection.execute(
                """
                select s.id, s.prospect_id, s.version, s.screening_config_version,
                       s.industry_vi, s.years_operating, s.revenue_band_vi,
                       s.legal_status_vi, s.credit_history_vi,
                       s.risk_appetite_note_vi, s.details, s.created_by, s.created_at
                from public.prospect_screening_snapshots as s
                join public.prospects as p on p.id = s.prospect_id
                where s.prospect_id = %s and p.created_by = %s
                order by s.version desc
                limit 1
                """,
                (prospect_id, created_by),
            )
            snapshot_row = await cursor.fetchone()
            latest_snapshot = (
                _snapshot_from_row(snapshot_row) if snapshot_row is not None else None
            )

            cursor = await connection.execute(
                """
                select d.id, d.prospect_id, d.decision, d.rationale_vi,
                       d.decided_by, d.created_at
                from public.prospect_contact_decisions as d
                join public.prospects as p on p.id = d.prospect_id
                where d.prospect_id = %s and p.created_by = %s
                order by d.created_at, d.id
                """,
                (prospect_id, created_by),
            )
            decision_rows = await cursor.fetchall()

        decisions = tuple(_decision_from_row(decision_row) for decision_row in decision_rows)
        return ProspectDetail(
            prospect=prospect, latest_snapshot=latest_snapshot, decisions=decisions
        )

    async def append_screening_snapshot(
        self,
        *,
        prospect_id: UUID,
        created_by: UUID,
        screening_config_version: str,
        industry_vi: str | None,
        years_operating: int | None,
        revenue_band_vi: str | None,
        legal_status_vi: str | None,
        credit_history_vi: str | None,
        risk_appetite_note_vi: str | None,
        details: Mapping[str, object],
    ) -> ScreeningSnapshot:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await self._require_owned(connection, prospect_id, created_by)
                cursor = await connection.execute(
                    """
                    select coalesce(max(version), 0) + 1
                    from public.prospect_screening_snapshots
                    where prospect_id = %s
                    """,
                    (prospect_id,),
                )
                version_row = await cursor.fetchone()
                next_version = int(cast(int, version_row[0])) if version_row else 1

                cursor = await connection.execute(
                    """
                    insert into public.prospect_screening_snapshots (
                      prospect_id, version, screening_config_version, industry_vi,
                      years_operating, revenue_band_vi, legal_status_vi,
                      credit_history_vi, risk_appetite_note_vi, details, created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id, created_at
                    """,
                    (
                        prospect_id,
                        next_version,
                        screening_config_version,
                        industry_vi,
                        years_operating,
                        revenue_band_vi,
                        legal_status_vi,
                        credit_history_vi,
                        risk_appetite_note_vi,
                        Jsonb(dict(details)),
                        created_by,
                    ),
                )
                row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("screening snapshot insert returned no row")
        return ScreeningSnapshot(
            id=cast(UUID, row[0]),
            prospect_id=prospect_id,
            version=next_version,
            screening_config_version=screening_config_version,
            industry_vi=industry_vi,
            years_operating=years_operating,
            revenue_band_vi=revenue_band_vi,
            legal_status_vi=legal_status_vi,
            credit_history_vi=credit_history_vi,
            risk_appetite_note_vi=risk_appetite_note_vi,
            details=dict(details),
            created_by=created_by,
            created_at=cast(datetime, row[1]),
        )

    async def record_contact_decision(
        self,
        *,
        prospect_id: UUID,
        created_by: UUID,
        decision: ContactDecision,
        rationale_vi: str,
        decided_by: UUID,
    ) -> ContactDecisionRecord:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                await self._require_owned(connection, prospect_id, created_by)
                cursor = await connection.execute(
                    """
                    insert into public.prospect_contact_decisions (
                      prospect_id, decision, rationale_vi, decided_by
                    ) values (%s, %s, %s, %s)
                    returning id, created_at
                    """,
                    (prospect_id, decision.value, rationale_vi, decided_by),
                )
                row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("contact decision insert returned no row")
        return ContactDecisionRecord(
            id=cast(UUID, row[0]),
            prospect_id=prospect_id,
            decision=decision,
            rationale_vi=rationale_vi,
            decided_by=decided_by,
            created_at=cast(datetime, row[1]),
        )

    @staticmethod
    async def _require_owned(
        connection: DatabaseConnection, prospect_id: UUID, created_by: UUID
    ) -> None:
        """Fail closed unless the actor owns the prospect (created_by scope)."""
        cursor = await connection.execute(
            """
            select 1 from public.prospects
            where id = %s and created_by = %s
            """,
            (prospect_id, created_by),
        )
        if await cursor.fetchone() is None:
            raise ProspectNotFound


def _prospect_from_row(row: Sequence[Any]) -> Prospect:
    return Prospect(
        id=cast(UUID, row[0]),
        name_vi=str(row[1]),
        industry_vi=cast("str | None", row[2]),
        years_operating=cast("int | None", row[3]),
        revenue_band_vi=cast("str | None", row[4]),
        legal_status_vi=cast("str | None", row[5]),
        notes_vi=cast("str | None", row[6]),
        created_by=cast(UUID, row[7]),
        created_at=cast(datetime, row[8]),
    )


def _snapshot_from_row(row: Sequence[Any]) -> ScreeningSnapshot:
    return ScreeningSnapshot(
        id=cast(UUID, row[0]),
        prospect_id=cast(UUID, row[1]),
        version=int(cast(int, row[2])),
        screening_config_version=str(row[3]),
        industry_vi=cast("str | None", row[4]),
        years_operating=cast("int | None", row[5]),
        revenue_band_vi=cast("str | None", row[6]),
        legal_status_vi=cast("str | None", row[7]),
        credit_history_vi=cast("str | None", row[8]),
        risk_appetite_note_vi=cast("str | None", row[9]),
        details=dict(cast("Mapping[str, object]", row[10] or {})),
        created_by=cast(UUID, row[11]),
        created_at=cast(datetime, row[12]),
    )


def _decision_from_row(row: Sequence[Any]) -> ContactDecisionRecord:
    return ContactDecisionRecord(
        id=cast(UUID, row[0]),
        prospect_id=cast(UUID, row[1]),
        decision=ContactDecision(str(row[2])),
        rationale_vi=str(row[3]),
        decided_by=cast(UUID, row[4]),
        created_at=cast(datetime, row[5]),
    )
