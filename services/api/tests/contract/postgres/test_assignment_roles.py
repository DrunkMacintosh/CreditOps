from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from creditops.infrastructure.postgres.repositories import PostgresCaseRepository

OFFICER_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class ScriptedCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class ScriptedConnection:
    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self.results = results
        self.executions: list[tuple[str, tuple[Any, ...] | list[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: tuple[Any, ...] | list[object] | None = None,
    ) -> ScriptedCursor:
        self.executions.append((query, params))
        return ScriptedCursor(self.results.pop(0) if self.results else [])


@pytest.mark.asyncio
async def test_list_assignment_roles_filters_active_rows_for_actor() -> None:
    case_id = uuid4()
    connection = ScriptedConnection([[("INTAKE_OFFICER",), ("UNDERWRITER",)]])
    repository = PostgresCaseRepository(connection)

    roles = await repository.list_assignment_roles(case_id, OFFICER_A)

    assert roles == frozenset({"INTAKE_OFFICER", "UNDERWRITER"})
    query, params = connection.executions[0]
    assert "public.case_assignments" in query
    assert "case_role" in query
    assert "revoked_at is null" in query
    assert params == (case_id, OFFICER_A)


@pytest.mark.asyncio
async def test_list_assignment_roles_returns_empty_frozenset_when_unassigned() -> None:
    connection = ScriptedConnection([[]])
    repository = PostgresCaseRepository(connection)

    roles = await repository.list_assignment_roles(uuid4(), OFFICER_A)

    assert roles == frozenset()


def test_case_assignment_roles_migration_is_additive_and_role_scoped() -> None:
    workspace = Path(__file__).resolve().parents[5]
    migration = workspace / "supabase/migrations/202607180008_case_assignment_roles.sql"

    assert migration.exists()
    sql = migration.read_text().lower()
    # Additive: extends the existing table, never rewrites the original migration.
    assert "alter table public.case_assignments" in sql
    assert "add column case_role" in sql
    # Closed synthetic role set, fully spelled out.
    for role in (
        "intake_officer",
        "underwriter",
        "legal_reviewer",
        "risk_reviewer",
        "ops_officer",
        "ops_checker",
        "action_authorizer",
        "monitoring_officer",
        "collections_officer",
        "auditor",
    ):
        assert f"'{role}'" in sql
    # Backfill then constrain, and swap the flat uniqueness for a per-role one.
    assert "set case_role = 'intake_officer'" in sql
    assert "set not null" in sql
    assert "drop constraint case_assignments_case_officer_key" in sql
    assert "unique (case_id, officer_id, case_role)" in sql
    # PROPOSED / synthetic, no official SHB mapping.
    assert "synthetic" in sql
    assert "proposed" in sql
