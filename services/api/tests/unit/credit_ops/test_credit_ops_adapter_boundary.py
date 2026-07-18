"""The credit-ops agent cannot modify upstream output: adapter-level proof.

Mirrors ``tests/unit/risk_review/test_postgres_adapter_boundary.py``: rather
than requiring a live Postgres instance, these tests inspect the ACTUAL SQL
statements the concrete adapter issues and prove, mechanically, that no
``insert``, ``update``, or ``delete`` targets ``underwriting_assessments``,
``legal_compliance_assessments``, or ``risk_review_assessments`` anywhere in
the module -- the only verb present against any upstream table is ``select``.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from creditops.infrastructure.postgres import credit_ops as adapter_module

_UPSTREAM_TABLES = (
    "underwriting_assessments",
    "legal_compliance_assessments",
    "risk_review_assessments",
)
_WRITE_VERBS = ("insert", "update", "delete", "merge", "truncate")


def _source_text() -> str:
    return Path(inspect.getfile(adapter_module)).read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Drop ``#``-prefixed comment lines and the module docstring so scans
    only see executable code / actual SQL strings."""

    lines = text.splitlines()
    if lines and lines[0].startswith('"""'):
        end = next(
            (i for i in range(1, len(lines)) if lines[i].strip().endswith('"""')),
            0,
        )
        lines = lines[end + 1 :]
    return "\n".join(line for line in lines if not line.strip().startswith("#"))


def test_no_write_verb_appears_near_an_upstream_table_name() -> None:
    text = _source_text().lower()
    for table in _UPSTREAM_TABLES:
        for match in re.finditer(re.escape(table), text):
            window_start = max(0, match.start() - 80)
            window = text[window_start : match.start()]
            for verb in _WRITE_VERBS:
                assert f"{verb} " not in window and not window.rstrip().endswith(verb), (
                    f"found write verb '{verb}' near upstream table '{table}': "
                    f"...{window!r}"
                )


def test_upstream_tables_are_only_ever_selected() -> None:
    text = _code_only(_source_text())
    for table in _UPSTREAM_TABLES:
        occurrences = [line for line in text.splitlines() if table in line]
        assert occurrences, f"expected the adapter to reference {table} at least once"
        for line in occurrences:
            lowered = line.strip().lower()
            assert (
                lowered.startswith("from public.")
                or lowered.startswith("select")
                or "from public." in lowered
            ), f"non-select reference to an upstream table: {line!r}"


def test_upstream_loaders_are_the_only_methods_touching_upstream_tables() -> None:
    source = _code_only(_source_text())
    class_body_start = source.index("class PostgresCreditOpsRepository")
    methods_and_bounds: list[tuple[str, int]] = []
    for match in re.finditer(r"\n    async def (\w+)\(", source[class_body_start:]):
        methods_and_bounds.append((match.group(1), match.start()))
    spans: list[tuple[str, str]] = []
    for index, (name, start) in enumerate(methods_and_bounds):
        end = (
            methods_and_bounds[index + 1][1]
            if index + 1 < len(methods_and_bounds)
            else len(source) - class_body_start
        )
        body = source[class_body_start + start : class_body_start + end]
        spans.append((name, body))

    touching_methods = {
        name for name, body in spans if any(table in body for table in _UPSTREAM_TABLES)
    }
    # ``load_upstream_view`` itself only delegates to these three read
    # helpers; the actual SQL naming the upstream tables lives exclusively
    # in them.
    assert touching_methods == {"_load_underwriting", "_load_legal", "_load_risk_review"}


def test_adapter_never_updates_the_package_or_gate_tables() -> None:
    # Append-only discipline at the adapter level: no UPDATE statement exists
    # anywhere in the module (the DB triggers enforce it too), and the
    # adapter never touches human_gates at all -- zero gate writes in agent
    # code; only api/credit_ops.py's human-triggered path writes a gate,
    # through the orchestration repository.
    text = _code_only(_source_text()).lower()
    assert "update public." not in text
    assert "delete from public." not in text
    assert "human_gates" not in text
