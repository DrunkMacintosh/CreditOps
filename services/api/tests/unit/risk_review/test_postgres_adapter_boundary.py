"""(a) The checker cannot modify maker output: adapter-level source proof.

Mirrors the migration text-scan pattern already used by this repo
(tests/unit/infrastructure/test_api_role_migration.py): rather than requiring
a live Postgres instance, this test inspects the ACTUAL SQL statements the
concrete adapter issues and proves, mechanically, that no ``insert``,
``update``, or ``delete`` targets ``underwriting_assessments`` or
``legal_compliance_assessments`` anywhere in the module -- the only verb
present against either table is ``select``.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from creditops.infrastructure.postgres import risk_review as adapter_module

_MAKER_TABLES = ("underwriting_assessments", "legal_compliance_assessments")
_WRITE_VERBS = ("insert", "update", "delete", "merge", "truncate")


def _source_text() -> str:
    return Path(inspect.getfile(adapter_module)).read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Drop ``#``-prefixed comment lines and the module docstring so scans
    only see executable code / actual SQL strings, not narration that
    happens to name a maker table."""

    lines = text.splitlines()
    if lines and lines[0].startswith('"""'):
        # Skip the module docstring block.
        end = next(
            (i for i in range(1, len(lines)) if lines[i].strip().endswith('"""')),
            0,
        )
        lines = lines[end + 1 :]
    return "\n".join(
        line for line in lines if not line.strip().startswith("#")
    )


def test_no_write_verb_appears_near_a_maker_table_name() -> None:
    text = _source_text().lower()
    for table in _MAKER_TABLES:
        # Every mention of the table is inside a SQL string; find each
        # occurrence and look at a bounded window of text immediately before
        # it for a write verb applied to it (e.g. "insert into ... underwriting_
        # assessments", "update public.underwriting_assessments").
        for match in re.finditer(re.escape(table), text):
            window_start = max(0, match.start() - 80)
            window = text[window_start:match.start()]
            for verb in _WRITE_VERBS:
                assert f"{verb} " not in window and not window.rstrip().endswith(verb), (
                    f"found write verb '{verb}' near maker table '{table}': "
                    f"...{window!r}"
                )


def test_maker_tables_are_only_ever_selected() -> None:
    text = _code_only(_source_text())
    for table in _MAKER_TABLES:
        occurrences = [
            line
            for line in text.splitlines()
            if table in line
        ]
        assert occurrences, f"expected the adapter to reference {table} at least once"
        for line in occurrences:
            lowered = line.strip().lower()
            # Every line naming a maker table must be a FROM/JOIN clause of a
            # read, never the target of a write statement.
            assert lowered.startswith("from public.") or lowered.startswith(
                "select"
            ) or "from public." in lowered, (
                f"non-select reference to a maker table: {line!r}"
            )


def test_load_maker_outputs_is_the_only_public_method_touching_maker_tables() -> None:
    source = _code_only(_source_text())
    class_body_start = source.index("class PostgresRiskReviewRepository")
    methods_and_bounds: list[tuple[str, int, int]] = []
    for match in re.finditer(r"\n    async def (\w+)\(", source[class_body_start:]):
        methods_and_bounds.append((match.group(1), match.start(), 0))
    # Compute each method's textual span by using the next method's start.
    spans: list[tuple[str, str]] = []
    for index, (name, start, _unused) in enumerate(methods_and_bounds):
        end = (
            methods_and_bounds[index + 1][1]
            if index + 1 < len(methods_and_bounds)
            else len(source) - class_body_start
        )
        body = source[class_body_start + start : class_body_start + end]
        spans.append((name, body))

    touching_methods = {
        name
        for name, body in spans
        if any(table in body for table in _MAKER_TABLES)
    }
    # ``load_maker_outputs`` itself only delegates to these two read helpers;
    # the actual SQL naming the maker tables lives exclusively in them.
    assert touching_methods == {"_load_underwriting", "_load_legal"}
