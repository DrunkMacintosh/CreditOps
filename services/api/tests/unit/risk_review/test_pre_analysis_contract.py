"""Contract tests for the blind Pass A pre-analysis SQL surface.

Mirrors the source-text-scan style of ``test_postgres_adapter_boundary`` (no
live Postgres needed): proves mechanically that the ``risk_pre_analyses``
adapter methods are append-only, idempotent, and structurally incapable of
touching a maker table, and that the migration declares the table append-only,
deduplicated, object-checked, and RLS-guarded.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from creditops.infrastructure.postgres import risk_review as adapter_module

_MAKER_TABLES = ("underwriting_assessments", "legal_compliance_assessments")
_MIGRATION = (
    Path(__file__).resolve().parents[5]
    / "supabase"
    / "migrations"
    / "202607180015_risk_pre_analysis.sql"
)


def _adapter_source() -> str:
    return Path(inspect.getfile(adapter_module)).read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Drop ``#``-prefixed comment lines so scans see only executable code /
    SQL strings, never narration that happens to name a maker table."""

    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


def _method_body(source: str, name: str) -> str:
    match = re.search(rf"\n    async def {name}\(", source)
    assert match is not None, f"expected adapter method {name}"
    rest = source[match.start() + 1 :]
    next_method = re.search(r"\n    (?:async def|def|@staticmethod)", rest)
    body = rest[: next_method.start()] if next_method else rest
    return _code_only(body)


def test_pre_analysis_adapter_methods_exist() -> None:
    source = _adapter_source()
    for name in ("load_blind_evidence_view", "find_pre_analysis", "persist_pre_analysis"):
        assert f"async def {name}(" in source


def test_persist_pre_analysis_is_idempotent_append_only() -> None:
    body = _method_body(_adapter_source(), "persist_pre_analysis")
    assert "insert into public.risk_pre_analyses" in body
    # Idempotent on the inbox-dedup key: a redelivery resolves to the existing
    # row instead of a second blind pre-analysis.
    assert "on conflict (case_id, case_version, task_id) do nothing" in body
    # Append-only: no update/delete of an existing row.
    for verb in ("update ", "delete "):
        assert verb not in body.lower()


def test_blind_pass_methods_never_touch_a_maker_table() -> None:
    for name in ("load_blind_evidence_view", "find_pre_analysis", "persist_pre_analysis"):
        body = _method_body(_adapter_source(), name).lower()
        for table in _MAKER_TABLES:
            assert table not in body, (
                f"{name} references maker table {table}: the blind pass must be "
                "structurally incapable of loading maker output"
            )


def test_blind_evidence_view_reads_confirmed_facts_only() -> None:
    body = _method_body(_adapter_source(), "load_blind_evidence_view").lower()
    assert "from public.confirmed_facts" in body


def test_migration_declares_append_only_deduped_object_checked_rls() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8").lower()
    assert "create table public.risk_pre_analyses" in sql
    # Append-only trigger.
    assert "risk_pre_analyses_are_append_only" in sql
    assert "reject_append_only_mutation" in sql
    # Inbox-dedup unique key.
    assert "unique (case_id, case_version, task_id)" in sql
    # The analysis payload must be a JSON object.
    assert "jsonb_typeof(analysis) = 'object'" in sql
    # Row-level security is enabled, forced, and select is assignment-scoped.
    assert "enable row level security" in sql
    assert "force row level security" in sql
    assert "risk_pre_analyses_select_assigned" in sql
    # Composite task/case FK, like the checker assessment table.
    assert "references public.processing_tasks(id, case_id, case_version)" in sql
    # No write access granted to non-service roles.
    assert "revoke all on public.risk_pre_analyses from public, anon, authenticated;" in sql


def test_migration_grants_no_maker_table_access() -> None:
    # Scan only executable SQL (drop ``--`` comment lines): the migration's
    # own narration explains that it grants no maker access, so it names the
    # maker tables in prose -- but no STATEMENT may reference them.
    lines = _MIGRATION.read_text(encoding="utf-8").lower().splitlines()
    code = "\n".join(line for line in lines if not line.strip().startswith("--"))
    for table in _MAKER_TABLES:
        assert table not in code, (
            "the blind pre-analysis migration must not reference a maker table in any statement"
        )
