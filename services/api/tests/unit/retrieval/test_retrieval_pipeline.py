"""``RunRetrieval`` orchestration tests (application/retrieval/pipeline.py).

A fake port and a fake model gateway drive the section 12.2 pipeline end to end:
seed -> traverse -> lexical + vector merge (union, dedupe, stable best-rank) ->
hydrate -> pack -> persist.  The tests pin the merge order, the LEXICAL-ONLY
degradation when no gateway is injected, the honest empty result when nothing is
reached, and the STALE / UNAUTHORIZED_SCOPE / OUT_OF_BUDGET exclusion paths.

All identifiers are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

from creditops.application.ports.model_gateway import (
    EmbeddingRequest,
    InferenceResult,
    KIERequest,
    ReasonRequest,
    TableRequest,
    VisionRequest,
)
from creditops.application.ports.retrieval import (
    CandidatePassage,
    HydratedPassage,
    PersistableHit,
    ScoredPassage,
)
from creditops.application.retrieval.pipeline import RunRetrieval
from creditops.domain.retrieval import RetrievalRequest, SeedNodeRef

CASE = UUID("10000000-0000-0000-0000-0000000000f1")
CASE_VERSION = 2
DOC = UUID("61000000-0000-0000-0000-0000000000f1")
REGION = UUID("62000000-0000-0000-0000-0000000000f1")
P1 = UUID("63000000-0000-0000-0000-000000000001")
P2 = UUID("63000000-0000-0000-0000-000000000002")
HASH1 = "a" * 64
HASH2 = "b" * 64
CORRELATION = "corr-1"


class FakePort:
    def __init__(
        self,
        *,
        candidates: Sequence[CandidatePassage] = (),
        lexical: Sequence[ScoredPassage] = (),
        vector: Sequence[ScoredPassage] = (),
        hydrated: Sequence[HydratedPassage] = (),
    ) -> None:
        self._candidates = tuple(candidates)
        self._lexical = tuple(lexical)
        self._vector = tuple(vector)
        self._hydrated = tuple(hydrated)
        self.lexical_calls: list[tuple[UUID, ...]] = []
        self.vector_calls: list[tuple[float, ...]] = []
        self.hydrate_calls: list[tuple[UUID, ...]] = []
        self.persisted: dict[str, object] | None = None

    async def traverse_evidence_graph(
        self, *, case_id, case_version, seeds, max_hops, max_nodes
    ) -> tuple[CandidatePassage, ...]:
        return self._candidates

    async def lexical_search(
        self, *, case_id, case_version, query_text, candidate_passage_ids, limit
    ) -> tuple[ScoredPassage, ...]:
        self.lexical_calls.append(tuple(candidate_passage_ids))
        return self._lexical

    async def vector_search(
        self, *, case_id, case_version, query_embedding, candidate_passage_ids, limit
    ) -> tuple[ScoredPassage, ...]:
        self.vector_calls.append(tuple(query_embedding))
        return self._vector

    async def hydrate_passages(
        self, *, case_id, case_version, passage_ids
    ) -> tuple[HydratedPassage, ...]:
        self.hydrate_calls.append(tuple(passage_ids))
        return self._hydrated

    async def persist_query_and_hits(
        self,
        *,
        case_id,
        case_version,
        task_id,
        query_text_vi,
        seed_node_refs,
        filters: Mapping[str, object],
        hits: Sequence[PersistableHit],
    ) -> UUID:
        self.persisted = {
            "filters": dict(filters),
            "hits": tuple(hits),
            "seeds": tuple(seed_node_refs),
            "task_id": task_id,
        }
        return UUID("70000000-0000-0000-0000-0000000000f1")


class FakeGateway:
    def __init__(self, vector: Sequence[float]) -> None:
        self._vector = tuple(vector)
        self.embed_calls: list[EmbeddingRequest] = []

    async def embed(self, request: EmbeddingRequest) -> InferenceResult:
        self.embed_calls.append(request)
        return InferenceResult(
            capability="embedding",
            provider="FPT",
            case_id=request.case_id,
            endpoint_id="mock-endpoint",
            model_id="mock-embedding",
            payload=[list(self._vector)],
            prompt_version="1",
            schema_version="1",
            route_version="1",
            correlation_id=request.correlation_id,
            started_at=datetime.now(UTC),
            latency_ms=1,
        )

    async def reason(self, request: ReasonRequest) -> InferenceResult:
        raise NotImplementedError

    async def extract_kie(self, request: KIERequest) -> InferenceResult:
        raise NotImplementedError

    async def extract_table(self, request: TableRequest) -> InferenceResult:
        raise NotImplementedError

    async def inspect_vision(self, request: VisionRequest) -> InferenceResult:
        raise NotImplementedError


def _candidate(passage_id: UUID) -> CandidatePassage:
    return CandidatePassage(
        passage_id=passage_id, document_version_id=DOC, page_region_id=REGION
    )


def _hydrated(
    passage_id: UUID,
    *,
    text: str = "Doanh thu thuan (mo phong).",
    passage_hash: str = HASH1,
    case_version: int = CASE_VERSION,
) -> HydratedPassage:
    return HydratedPassage(
        passage_id=passage_id,
        case_id=CASE,
        case_version=case_version,
        document_version_id=DOC,
        page_number=1,
        page_region_id=REGION,
        passage_text=text,
        passage_hash=passage_hash,
    )


def _request(*, budget_tokens: int = 10_000) -> RetrievalRequest:
    return RetrievalRequest(
        case_id=CASE,
        case_version=CASE_VERSION,
        query_text="doanh thu thuan",
        seeds=(SeedNodeRef(kind="CONFIRMED_FACT", node_id=P1),),
        max_hops=2,
        max_nodes=50,
        budget_tokens=budget_tokens,
    )


# -- hybrid merge -------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_merges_legs_dedupes_and_orders_by_best_rank() -> None:
    # lexical ranks P1 then P2; vector ranks P2 (closest) then P1.  Both P1 and
    # P2 have best-rank 0 -> tie broken by first appearance (lexical leg) -> P1, P2.
    port = FakePort(
        candidates=(_candidate(P1), _candidate(P2)),
        lexical=(ScoredPassage(P1, 0.9), ScoredPassage(P2, 0.4)),
        vector=(ScoredPassage(P2, 0.1), ScoredPassage(P1, 0.3)),
        hydrated=(_hydrated(P1, passage_hash=HASH1), _hydrated(P2, passage_hash=HASH2)),
    )
    gateway = FakeGateway((0.1, 0.2, 0.3))
    pipeline = RunRetrieval(port, gateway=gateway)

    result = await pipeline.execute(_request(), correlation_id=CORRELATION)

    assert [hit.passage_id for hit in result.hits] == [P1, P2]
    assert [hit.rank for hit in result.hits] == [1, 2]
    # Each hit carries the score from BOTH legs (dedupe kept both signals).
    assert result.hits[0].lexical_score == 0.9
    assert result.hits[0].vector_score == 0.3
    assert result.hits[1].lexical_score == 0.4
    assert result.hits[1].vector_score == 0.1
    # The query embedding came from the gateway, once, for the query text.
    assert len(gateway.embed_calls) == 1
    assert gateway.embed_calls[0].texts == ["doanh thu thuan"]
    assert port.vector_calls == [(0.1, 0.2, 0.3)]
    # The packed context is the two ORIGINAL passages, and the trace was persisted.
    assert result.packed_context_vi.count("Doanh thu thuan (mo phong).") == 2
    assert port.persisted is not None
    assert port.persisted["filters"]["mode"] == "HYBRID"  # type: ignore[index]
    assert len(port.persisted["hits"]) == 2  # type: ignore[arg-type]


# -- lexical-only degradation -------------------------------------------------


@pytest.mark.asyncio
async def test_no_gateway_runs_lexical_only_without_a_fake_embedding() -> None:
    port = FakePort(
        candidates=(_candidate(P1),),
        lexical=(ScoredPassage(P1, 0.9),),
        hydrated=(_hydrated(P1),),
    )
    pipeline = RunRetrieval(port, gateway=None)

    result = await pipeline.execute(_request(), correlation_id=CORRELATION)

    assert [hit.passage_id for hit in result.hits] == [P1]
    assert result.hits[0].vector_score is None
    # No vector leg ran at all -- degradation is skipping the call, not faking it.
    assert port.vector_calls == []
    assert port.persisted is not None
    assert port.persisted["filters"]["mode"] == "LEXICAL_ONLY"  # type: ignore[index]


# -- honest empty result ------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_candidates_returns_honest_empty_result() -> None:
    port = FakePort(candidates=())
    pipeline = RunRetrieval(port, gateway=None)

    result = await pipeline.execute(_request(), correlation_id=CORRELATION)

    assert result.hits == ()
    assert result.packed_context_vi == ""
    assert result.token_estimate == 0
    assert result.excluded == ()
    # No ranking legs run when the graph reaches nothing, but the empty trace is
    # still recorded (honest, non-fabricated).
    assert port.lexical_calls == []
    assert port.persisted is not None
    assert port.persisted["hits"] == ()


@pytest.mark.asyncio
async def test_candidates_but_no_ranked_hits_is_empty() -> None:
    # Traversal finds a candidate but neither leg ranks it (e.g. no lexical match
    # and no gateway) -> empty, honest result.
    port = FakePort(candidates=(_candidate(P1),), lexical=())
    pipeline = RunRetrieval(port, gateway=None)

    result = await pipeline.execute(_request(), correlation_id=CORRELATION)

    assert result.hits == ()
    assert result.packed_context_vi == ""
    assert port.hydrate_calls == []  # nothing to hydrate


# -- exclusions ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_passage_missing_at_hydration_is_excluded_stale() -> None:
    port = FakePort(
        candidates=(_candidate(P1), _candidate(P2)),
        lexical=(ScoredPassage(P1, 0.9), ScoredPassage(P2, 0.4)),
        hydrated=(_hydrated(P1),),  # P2 went stale between ranking and hydration
    )
    pipeline = RunRetrieval(port, gateway=None)

    result = await pipeline.execute(_request(), correlation_id=CORRELATION)

    assert [hit.passage_id for hit in result.hits] == [P1]
    assert len(result.excluded) == 1
    assert result.excluded[0].passage_id == P2
    assert result.excluded[0].reason == "STALE"


@pytest.mark.asyncio
async def test_out_of_scope_hydrated_passage_is_excluded() -> None:
    port = FakePort(
        candidates=(_candidate(P1),),
        lexical=(ScoredPassage(P1, 0.9),),
        hydrated=(_hydrated(P1, case_version=CASE_VERSION + 1),),  # scope mismatch
    )
    pipeline = RunRetrieval(port, gateway=None)

    result = await pipeline.execute(_request(), correlation_id=CORRELATION)

    assert result.hits == ()
    assert result.excluded[0].passage_id == P1
    assert result.excluded[0].reason == "UNAUTHORIZED_SCOPE"


@pytest.mark.asyncio
async def test_over_budget_hit_is_excluded_and_not_persisted() -> None:
    # P1 fills the budget (10 tokens ~ 40 chars); P2 cannot fit.
    port = FakePort(
        candidates=(_candidate(P1), _candidate(P2)),
        lexical=(ScoredPassage(P1, 0.9), ScoredPassage(P2, 0.4)),
        hydrated=(
            _hydrated(P1, text="A" * 40, passage_hash=HASH1),
            _hydrated(P2, text="B" * 40, passage_hash=HASH2),
        ),
    )
    pipeline = RunRetrieval(port, gateway=None)

    result = await pipeline.execute(_request(budget_tokens=10), correlation_id=CORRELATION)

    assert [hit.passage_id for hit in result.hits] == [P1]
    assert result.token_estimate == 10
    assert result.excluded[0].passage_id == P2
    assert result.excluded[0].reason == "OUT_OF_BUDGET"
    # Only the delivered hit is written to the trace.
    assert port.persisted is not None
    assert len(port.persisted["hits"]) == 1  # type: ignore[arg-type]
