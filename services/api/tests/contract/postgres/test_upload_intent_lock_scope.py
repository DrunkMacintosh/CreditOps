"""Contract test: ``get_intent`` locks ONLY the upload-intent row.

Live regression (2026-07-19, correlation 51a3c8d9-...): the query joins
``case_assignments`` for authorization, and a bare ``FOR UPDATE`` locks every
joined table — demanding an UPDATE privilege on ``case_assignments`` that the
``creditops_api`` role deliberately does not hold, so every real
upload-completion 500'd with InsufficientPrivilege (pgTAP CI runs as a
superuser and could not catch it). The lock must be scoped ``FOR UPDATE OF
intent``. All identifiers here are synthetic.
"""

from __future__ import annotations

import re
from uuid import UUID

import pytest

from creditops.infrastructure.postgres.repositories import PostgresUploadRepository

INTENT = UUID("419c7380-1389-4cbd-a551-57cd7e8b2b51")
OFFICER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class Cursor:
    async def fetchone(self) -> None:
        return None


class Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, query: str, params: object = None) -> Cursor:
        self.statements.append(query)
        return Cursor()


@pytest.mark.asyncio
async def test_get_intent_locks_only_the_intent_row() -> None:
    connection = Connection()
    repository = PostgresUploadRepository(connection)  # type: ignore[arg-type]
    assert await repository.get_intent(INTENT, OFFICER) is None

    sql = " ".join(connection.statements[0].lower().split())
    # Authorization join stays.
    assert "join public.case_assignments" in sql
    # The row lock is scoped to the intent alias only — a bare FOR UPDATE would
    # require UPDATE privilege on the joined case_assignments table.
    assert "for update of intent" in sql
    assert re.search(r"for update(?!\s+of\s+intent)", sql) is None
