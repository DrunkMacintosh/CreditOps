"""Aggregating benchmark runner over a provider-neutral inference gateway.

The runner drives the committed holdout through an
:class:`~creditops.application.ports.model_gateway.InferenceGateway` and folds the
per-case :class:`~creditops.benchmarks.scoring.ScoreOutcome` values into one
:class:`CapabilityReport`. It performs no I/O of its own beyond the gateway calls
and never writes files, mutates the benchmark registry, or activates a route.

Aggregation is deterministic given deterministic gateway responses: the score is
simply the fraction of passing cases, and ``passed`` is that score measured
against the PROPOSED threshold for the capability.

All holdout data is synthetic. Toàn bộ dữ liệu trong gói này là dữ liệu tổng hợp,
được tạo riêng cho mục đích trình diễn.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from creditops.application.ports.model_gateway import (
    EmbeddingRequest,
    InferenceError,
    InferenceGateway,
    InferenceUnavailableError,
    ReasonRequest,
)
from creditops.benchmarks.holdout import (
    EmbeddingCase,
    ReasoningCase,
    embedding_cases,
    reasoning_cases,
)
from creditops.benchmarks.scoring import (
    PROPOSED_EMBEDDING_ORDERING_THRESHOLD,
    PROPOSED_REASONING_PASS_THRESHOLD,
)
from creditops.infrastructure.fpt.catalog import CapabilityName


@dataclass(frozen=True)
class CaseResult:
    """The verdict for one holdout case within a run."""

    case_id: str
    kind: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class CapabilityReport:
    """The aggregated verdict for one capability against one live endpoint.

    Carries only non-secret call identity (``model_id``, ``endpoint_id``, the
    route/prompt/schema versions) — never the API key or endpoint URL — so the
    report is safe to render into committed evidence.
    """

    capability: CapabilityName
    model_id: str
    endpoint_id: str
    route_version: str
    prompt_version: str
    schema_version: str
    threshold: float
    started_at: datetime
    results: tuple[CaseResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def score(self) -> float:
        if not self.results:
            return 0.0
        return self.passed_count / self.total

    @property
    def passed(self) -> bool:
        return self.total > 0 and self.score >= self.threshold

    @property
    def failed_results(self) -> tuple[CaseResult, ...]:
        return tuple(result for result in self.results if not result.passed)


async def run_reasoning_benchmark(
    gateway: InferenceGateway,
    *,
    model_id: str,
    endpoint_id: str,
    route_version: str,
    prompt_version: str,
    schema_version: str,
    cases: Sequence[ReasoningCase] | None = None,
    threshold: float = PROPOSED_REASONING_PASS_THRESHOLD,
    correlation_prefix: str = "bench-reasoning",
) -> CapabilityReport:
    """Run the reasoning holdout and aggregate a :class:`CapabilityReport`."""

    selected = tuple(cases) if cases is not None else reasoning_cases()
    started_at = datetime.now(UTC)
    results: list[CaseResult] = []
    for index, case in enumerate(selected):
        try:
            result = await gateway.reason(
                ReasonRequest(
                    correlation_id=f"{correlation_prefix}-{index}",
                    case_id=uuid4(),
                    content=case.content,
                    response_schema=case.response_schema,
                    system_context=case.system_context,
                )
            )
        except InferenceUnavailableError:
            raise
        except InferenceError as exc:
            results.append(
                CaseResult(case.case_id, case.kind, False, f"inference error: {type(exc).__name__}")
            )
            continue
        outcome = case.score(result.payload)
        results.append(CaseResult(case.case_id, case.kind, outcome.passed, outcome.reason))
    return CapabilityReport(
        capability="reasoning",
        model_id=model_id,
        endpoint_id=endpoint_id,
        route_version=route_version,
        prompt_version=prompt_version,
        schema_version=schema_version,
        threshold=threshold,
        started_at=started_at,
        results=tuple(results),
    )


async def run_embedding_benchmark(
    gateway: InferenceGateway,
    *,
    model_id: str,
    endpoint_id: str,
    route_version: str,
    prompt_version: str,
    schema_version: str,
    cases: Sequence[EmbeddingCase] | None = None,
    threshold: float = PROPOSED_EMBEDDING_ORDERING_THRESHOLD,
    correlation_prefix: str = "bench-embedding",
) -> CapabilityReport:
    """Run the embedding holdout and aggregate a :class:`CapabilityReport`."""

    selected = tuple(cases) if cases is not None else embedding_cases()
    started_at = datetime.now(UTC)
    results: list[CaseResult] = []
    for index, case in enumerate(selected):
        try:
            result = await gateway.embed(
                EmbeddingRequest(
                    correlation_id=f"{correlation_prefix}-{index}",
                    case_id=uuid4(),
                    texts=case.texts,
                )
            )
        except InferenceUnavailableError:
            raise
        except InferenceError as exc:
            results.append(
                CaseResult(case.case_id, case.kind, False, f"inference error: {type(exc).__name__}")
            )
            continue
        outcome = case.score(result.payload)
        results.append(CaseResult(case.case_id, case.kind, outcome.passed, outcome.reason))
    return CapabilityReport(
        capability="embedding",
        model_id=model_id,
        endpoint_id=endpoint_id,
        route_version=route_version,
        prompt_version=prompt_version,
        schema_version=schema_version,
        threshold=threshold,
        started_at=started_at,
        results=tuple(results),
    )
