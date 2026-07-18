"""P0-B INDEPENDENT AUDIT -- graph-guided retrieval actually consumes the graph.

NEW standalone test proving item (13): the retrieval pipeline DOES invoke
``traverse_evidence_graph`` (it is a real production consumer of the materialized
typed edges, not dead code), scopes it to the request's own case_id + version,
and lets ONLY the graph-reachable candidate passages gate the downstream ranking
legs -- a passage the graph did not reach can never be ranked or delivered.

A recording fake port stands in for the Postgres adapter (the case+version+stale
SQL scoping of the real ``traverse_evidence_graph`` is separately covered by the
adapter contract suite; here we verify the application wiring / scope hand-off).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.retrieval import (
    CandidatePassage,
    HydratedPassage,
    PersistableHit,
    ScoredPassage,
)
from creditops.application.retrieval.pipeline import RunRetrieval
from creditops.domain.retrieval import RetrievalRequest, SeedNodeRef

CASE = UUID("10000000-0000-0000-0000-0000000000c1")
OTHER_CASE = UUID("19999999-0000-0000-0000-0000000000c1")
CASE_VERSION = 5
DOC = UUID("61000000-0000-0000-0000-0000000000c1")
REGION = UUID("62000000-0000-0000-0000-0000000000c1")
REACHED = UUID("63000000-0000-0000-0000-0000000000c1")
UNREACHED = UUID("6300dead-0000-0000-0000-0000000000c1")
HASH = "a" * 64


class RecordingPort:
    def __init__(self, candidates: tuple[CandidatePassage, ...]) -> None:
        self._candidates = candidates
        self.traverse_calls: list[dict[str, object]] = []
        self.lexical_calls: list[dict[str, object]] = []
        self.hydrate_calls: list[dict[str, object]] = []

    async def traverse_evidence_graph(
        self,
        *,
        case_id: UUID,
        case_version: int,
        seeds: Sequence[SeedNodeRef],
        max_hops: int,
        max_nodes: int,
    ) -> tuple[CandidatePassage, ...]:
        self.traverse_calls.append(
            {
                "case_id": case_id,
                "case_version": case_version,
                "seeds": tuple(seeds),
                "max_hops": max_hops,
                "max_nodes": max_nodes,
            }
        )
        return self._candidates

    async def lexical_search(
        self,
        *,
        case_id: UUID,
        case_version: int,
        query_text: str,
        candidate_passage_ids: Sequence[UUID],
        limit: int,
    ) -> tuple[ScoredPassage, ...]:
        self.lexical_calls.append(
            {
                "case_id": case_id,
                "case_version": case_version,
                "candidate_passage_ids": tuple(candidate_passage_ids),
            }
        )
        # Rank every passage the caller offers; the caller must only ever offer
        # graph-reachable ids.
        return tuple(
            ScoredPassage(passage_id=pid, score=1.0) for pid in candidate_passage_ids
        )

    async def vector_search(self, **_: object) -> tuple[ScoredPassage, ...]:
        return ()

    async def hydrate_passages(
        self,
        *,
        case_id: UUID,
        case_version: int,
        passage_ids: Sequence[UUID],
    ) -> tuple[HydratedPassage, ...]:
        self.hydrate_calls.append({"passage_ids": tuple(passage_ids)})
        return tuple(
            HydratedPassage(
                passage_id=pid,
                case_id=case_id,
                case_version=case_version,
                document_version_id=DOC,
                page_number=1,
                page_region_id=REGION,
                passage_text="Doanh thu thuan.",
                passage_hash=HASH,
            )
            for pid in passage_ids
        )

    async def persist_query_and_hits(
        self,
        *,
        case_id: UUID,
        case_version: int,
        task_id: UUID | None,
        query_text_vi: str,
        seed_node_refs: Sequence[SeedNodeRef],
        filters: Mapping[str, object],
        hits: Sequence[PersistableHit],
    ) -> UUID:
        return uuid4()


def _request() -> RetrievalRequest:
    return RetrievalRequest(
        case_id=CASE,
        case_version=CASE_VERSION,
        query_text="doanh thu thuan",
        seeds=(SeedNodeRef(kind="CONFIRMED_FACT", node_id=REACHED),),
        max_hops=3,
        max_nodes=200,
        budget_tokens=4000,
    )


@pytest.mark.asyncio
async def test_pipeline_invokes_graph_traversal_scoped_to_the_request_case() -> None:
    port = RecordingPort(
        candidates=(
            CandidatePassage(
                passage_id=REACHED, document_version_id=DOC, page_region_id=REGION
            ),
        )
    )

    await RunRetrieval(port).execute(_request(), correlation_id="corr-1")

    assert len(port.traverse_calls) == 1
    call = port.traverse_calls[0]
    assert call["case_id"] == CASE
    assert call["case_version"] == CASE_VERSION
    assert call["case_id"] != OTHER_CASE
    # The bounded-walk ceilings are passed through, not fabricated.
    assert call["max_hops"] == 3
    assert call["max_nodes"] == 200


@pytest.mark.asyncio
async def test_only_graph_reachable_candidates_gate_the_ranking_legs() -> None:
    # The graph reaches exactly REACHED; UNREACHED exists nowhere in the walk.
    port = RecordingPort(
        candidates=(
            CandidatePassage(
                passage_id=REACHED, document_version_id=DOC, page_region_id=REGION
            ),
        )
    )

    result = await RunRetrieval(port).execute(_request(), correlation_id="corr-2")

    # Lexical ranking was offered ONLY the graph-reachable passage id.
    assert port.lexical_calls[0]["candidate_passage_ids"] == (REACHED,)
    assert port.lexical_calls[0]["case_id"] == CASE
    assert port.lexical_calls[0]["case_version"] == CASE_VERSION
    delivered = {hit.passage_id for hit in result.hits}
    assert delivered == {REACHED}
    assert UNREACHED not in delivered


@pytest.mark.asyncio
async def test_empty_graph_reach_delivers_no_passages_and_ranks_nothing() -> None:
    # A run whose graph traversal reaches no node must not rank or hydrate
    # anything -- there is no path that reaches a passage off the graph.
    port = RecordingPort(candidates=())

    result = await RunRetrieval(port).execute(_request(), correlation_id="corr-3")

    assert result.hits == ()
    assert port.lexical_calls == []
    assert port.hydrate_calls == []
