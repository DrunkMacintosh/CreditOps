"""Unit tests for the benchmark runner and evidence rendering (no live calls).

A fake gateway returns canned outputs so these exercise aggregation, threshold
application, evidence rendering to a temp path, the record-snippet round-trip
back into ``FPTBenchmarkRecord``, and the FAILED path emitting no pass record.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from creditops.application.ports.model_gateway import (
    EmbeddingRequest,
    InferenceResult,
    InferenceUnavailableError,
    InferenceValidationError,
    ReasonRequest,
)
from creditops.benchmarks.evidence import (
    evidence_filename,
    evidence_ref,
    render_decision_log_row,
    render_evidence_markdown,
    render_record_snippet,
)
from creditops.benchmarks.holdout import ReasoningCase, embedding_cases, reasoning_cases
from creditops.benchmarks.runner import (
    CapabilityReport,
    CaseResult,
    run_embedding_benchmark,
    run_reasoning_benchmark,
)
from creditops.benchmarks.scoring import ScoreOutcome
from creditops.infrastructure.fpt.benchmark_records import FPTBenchmarkRecord
from creditops.infrastructure.fpt.catalog import (
    PROMPT_VERSION,
    ROUTE_VERSION,
    SCHEMA_VERSION,
)

_IDENTITY: dict[str, str] = {
    "model_id": "DeepSeek-V4-Flash",
    "endpoint_id": "ep-77",
    "route_version": ROUTE_VERSION,
    "prompt_version": PROMPT_VERSION,
    "schema_version": SCHEMA_VERSION,
}

_CLOSED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"ok": {"type": "boolean"}},
}


def _reason_result(payload: Any) -> InferenceResult:
    return InferenceResult(
        capability="reasoning",
        provider="FPT",
        case_id=uuid4(),
        endpoint_id="ep-77",
        model_id="DeepSeek-V4-Flash",
        payload=payload,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        route_version=ROUTE_VERSION,
        correlation_id="corr",
        started_at=datetime.now(UTC),
        latency_ms=1,
    )


def _embed_result(vectors: Any) -> InferenceResult:
    return InferenceResult(
        capability="embedding",
        provider="FPT",
        case_id=uuid4(),
        endpoint_id="ep-88",
        model_id="multilingual-e5-large",
        payload=vectors,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        route_version=ROUTE_VERSION,
        correlation_id="corr",
        started_at=datetime.now(UTC),
        latency_ms=1,
    )


class FakeReasonGateway:
    def __init__(self, payloads: list[Any]) -> None:
        self._payloads = list(payloads)
        self.requests: list[ReasonRequest] = []

    async def reason(self, request: ReasonRequest) -> InferenceResult:
        self.requests.append(request)
        return _reason_result(self._payloads.pop(0))

    async def embed(self, request: EmbeddingRequest) -> InferenceResult:
        raise AssertionError("embed should not be called")


class RaisingReasonGateway:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def reason(self, request: ReasonRequest) -> InferenceResult:
        raise self._error

    async def embed(self, request: EmbeddingRequest) -> InferenceResult:
        raise AssertionError("embed should not be called")


class FakeEmbedGateway:
    def __init__(self, vector_sets: list[Any]) -> None:
        self._vector_sets = list(vector_sets)

    async def embed(self, request: EmbeddingRequest) -> InferenceResult:
        return _embed_result(self._vector_sets.pop(0))

    async def reason(self, request: ReasonRequest) -> InferenceResult:
        raise AssertionError("reason should not be called")


def _ok_scorer(output: Any) -> ScoreOutcome:
    passed = isinstance(output, dict) and bool(output.get("ok"))
    return ScoreOutcome(passed, "ok" if passed else "not ok")


def _unit_case(case_id: str) -> ReasoningCase:
    return ReasoningCase(
        case_id=case_id,
        kind="unit",
        content="tài liệu tổng hợp",
        response_schema=_CLOSED_SCHEMA,
        scorer=_ok_scorer,
        reference_output={"ok": True},
    )


def _ordered_vectors(expected_order: tuple[int, ...]) -> list[list[float]]:
    count = len(expected_order)
    anchor = [1.0, 0.0]
    candidates: list[list[float] | None] = [None] * count
    for rank, candidate_index in enumerate(expected_order):
        theta = (rank + 1) * (math.pi / (2 * (count + 1)))
        candidates[candidate_index] = [math.cos(theta), math.sin(theta)]
    return [anchor, *[vector for vector in candidates if vector is not None]]


def _report(*, results: tuple[CaseResult, ...], threshold: float = 0.9) -> CapabilityReport:
    return CapabilityReport(
        capability="reasoning",
        model_id="DeepSeek-V4-Flash",
        endpoint_id="ep-77",
        route_version=ROUTE_VERSION,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        threshold=threshold,
        started_at=datetime(2026, 7, 18, tzinfo=UTC),
        results=results,
    )


# --- Aggregation + thresholds ------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_aggregation_below_threshold() -> None:
    cases = [_unit_case("c1"), _unit_case("c2"), _unit_case("c3")]
    gateway = FakeReasonGateway([{"ok": True}, {"ok": True}, {"ok": False}])
    report = await run_reasoning_benchmark(gateway, cases=cases, **_IDENTITY)
    assert report.total == 3
    assert report.passed_count == 2
    assert report.score == pytest.approx(2 / 3)
    assert report.passed is False
    assert {r.case_id for r in report.failed_results} == {"c3"}
    assert len(gateway.requests) == 3


@pytest.mark.asyncio
async def test_reasoning_aggregation_passes_when_all_pass() -> None:
    cases = [_unit_case("c1"), _unit_case("c2")]
    gateway = FakeReasonGateway([{"ok": True}, {"ok": True}])
    report = await run_reasoning_benchmark(gateway, cases=cases, **_IDENTITY)
    assert report.score == 1.0
    assert report.passed is True


@pytest.mark.asyncio
async def test_validation_error_marks_case_failed() -> None:
    gateway = RaisingReasonGateway(InferenceValidationError("schema mismatch"))
    report = await run_reasoning_benchmark(gateway, cases=[_unit_case("c1")], **_IDENTITY)
    assert report.total == 1
    assert report.passed_count == 0
    assert "inference error" in report.results[0].reason


@pytest.mark.asyncio
async def test_unavailable_endpoint_aborts_the_run() -> None:
    gateway = RaisingReasonGateway(InferenceUnavailableError("endpoint down"))
    with pytest.raises(InferenceUnavailableError):
        await run_reasoning_benchmark(gateway, cases=[_unit_case("c1")], **_IDENTITY)


@pytest.mark.asyncio
async def test_full_reasoning_holdout_passes_with_reference_outputs() -> None:
    cases = reasoning_cases()
    gateway = FakeReasonGateway([case.reference_output for case in cases])
    report = await run_reasoning_benchmark(gateway, cases=cases, **_IDENTITY)
    assert report.total == len(cases)
    assert report.score == 1.0
    assert report.passed is True


@pytest.mark.asyncio
async def test_embedding_ordering_aggregation() -> None:
    cases = embedding_cases()
    good = FakeEmbedGateway([_ordered_vectors(case.expected_order) for case in cases])
    report = await run_embedding_benchmark(good, cases=cases, **_IDENTITY)
    assert report.score == 1.0
    assert report.passed is True

    vector_sets = [_ordered_vectors(case.expected_order) for case in cases]
    vector_sets[0] = _ordered_vectors(tuple(reversed(cases[0].expected_order)))
    bad = FakeEmbedGateway(vector_sets)
    bad_report = await run_embedding_benchmark(bad, cases=cases, **_IDENTITY)
    assert bad_report.passed is False  # threshold is exact 1.0
    assert bad_report.failed_results[0].case_id == cases[0].case_id


# --- Evidence rendering ------------------------------------------------------


def test_evidence_renders_to_tmp_path_without_secrets(tmp_path: Any) -> None:
    report = _report(
        results=(
            CaseResult("reason-extract-loan-intake", "structured_extraction", True, "ok"),
            CaseResult("reason-abstain-net-profit", "abstention", True, "ok"),
        )
    )
    assert report.passed is True
    markdown = render_evidence_markdown(report)
    path = tmp_path / evidence_filename(report)
    path.write_text(markdown, encoding="utf-8")

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "PASS" in content
    assert "DeepSeek-V4-Flash" in content
    assert "ep-77" in content
    # No secret VALUE ever appears: no endpoint URL, API key, or bearer token.
    # (The prose may mention that secrets are deliberately absent.)
    lowered = content.lower()
    assert "https://" not in content
    assert "bearer" not in lowered
    assert "get_secret_value" not in lowered


def test_failed_evidence_is_marked_failed() -> None:
    report = _report(
        results=(
            CaseResult("a", "structured_extraction", True, "ok"),
            CaseResult("b", "abstention", False, "fabricated"),
        )
    )
    assert report.passed is False
    assert "FAILED" in render_evidence_markdown(report)


# --- Record snippet round-trip ----------------------------------------------


def test_record_snippet_round_trips_into_a_valid_record() -> None:
    report = _report(
        results=(CaseResult("a", "structured_extraction", True, "ok"),)
    )
    ref = evidence_ref(report)
    assert ref == "docs/benchmarks/reasoning-DeepSeek-V4-Flash-evidence.md"
    snippet = render_record_snippet(report, recorded_on="2026-07-18", evidence_ref=ref)

    record = eval(snippet, {"FPTBenchmarkRecord": FPTBenchmarkRecord})
    assert isinstance(record, FPTBenchmarkRecord)
    assert record.capability == "reasoning"
    assert record.model_id == "DeepSeek-V4-Flash"
    assert record.endpoint_id == "ep-77"
    assert record.route_version == ROUTE_VERSION
    assert record.prompt_version == PROMPT_VERSION
    assert record.schema_version == SCHEMA_VERSION
    assert record.passed is True
    assert record.evidence_ref == ref
    assert record.recorded_on == "2026-07-18"


def test_failed_run_emits_no_pass_record() -> None:
    report = _report(
        results=(
            CaseResult("a", "structured_extraction", True, "ok"),
            CaseResult("b", "abstention", False, "fabricated"),
        )
    )
    assert report.passed is False
    with pytest.raises(ValueError, match="FAILED"):
        render_record_snippet(
            report,
            recorded_on="2026-07-18",
            evidence_ref="docs/benchmarks/reasoning-DeepSeek-V4-Flash-evidence.md",
        )


def test_decision_log_row_shape() -> None:
    report = _report(results=(CaseResult("a", "structured_extraction", True, "ok"),))
    row = render_decision_log_row(
        report, recorded_on="2026-07-18", evidence_ref=evidence_ref(report)
    )
    assert row.startswith("| 2026-07-18 |")
    assert row.rstrip().endswith("|")
    assert "PROPOSED" in row
    assert row.count("|") == 7
