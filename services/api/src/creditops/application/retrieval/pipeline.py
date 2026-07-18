"""``RunRetrieval`` -- the graph-guided hybrid retrieval orchestrator (master
design sections 12, 12.2, 12.3).

The service runs the section 12.2 pipeline in order and honours the section 12.3
priority ladder (bounded graph traversal narrows scope BEFORE any ranking; the
model gateway is only ever asked for a query embedding, never for a decision):

    seeds
      -> traverse_evidence_graph (bounded, case+version scoped)
      -> lexical_search  +  (vector_search iff a gateway is injected)
      -> merge legs (union, dedupe by passage, stable order by best rank)
      -> hydrate_passages (immutable source + sha256 hash)
      -> pack_context (deterministic greedy pack to the token budget)
      -> persist_query_and_hits (append-only trace)

Degradation is explicit and fail-closed: with ``gateway is None`` the run is
LEXICAL-ONLY and records ``mode = LEXICAL_ONLY`` in the trace filters -- it never
fabricates a query embedding.  A run that reaches no passage returns an honest
empty ``RetrievalResult`` (empty hits, empty packed context).  Every dropped
passage is recorded as STALE / UNAUTHORIZED_SCOPE / OUT_OF_BUDGET; nothing is
silently discarded.

Consumption seam: an agent-worker context builder (master design section 10.2,
step 8 "run graph-guided retrieval") constructs the ``RetrievalRequest`` from the
authorized ContextManifest, calls ``execute`` with the task's correlation/ task
ids, and folds ``RetrievalResult.packed_context_vi`` into the untrusted-evidence
section of the model call -- then gates the model's claims with
``domain.retrieval.validate_citations`` against ``hit.passage_hash``.  This
service is deliberately not wired into ``api/`` or ``main.py`` here; it is a pure
application seam the processor layer will consume.
"""

from __future__ import annotations

from typing import NamedTuple
from uuid import UUID

from creditops.application.ports.model_gateway import (
    EmbeddingRequest,
    InferenceGateway,
)
from creditops.application.ports.retrieval import (
    EvidenceGraphRetrievalPort,
    PersistableHit,
    ScoredPassage,
)
from creditops.domain.retrieval import (
    ExcludedPassage,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
    pack_context,
)

#: Default per-leg fan-out.  Each search returns at most this many candidates
#: before the union/merge; the token budget (not this cap) bounds what is packed.
DEFAULT_LEXICAL_LIMIT = 50
DEFAULT_VECTOR_LIMIT = 50


class _MergedPassage(NamedTuple):
    passage_id: UUID
    lexical_score: float | None
    vector_score: float | None


def _merge_rankings(
    lexical: tuple[ScoredPassage, ...], vector: tuple[ScoredPassage, ...]
) -> tuple[_MergedPassage, ...]:
    """Union the two search legs, dedupe by passage, order by best rank.

    A passage's rank in a leg is its 0-based position in that leg (best first).
    The merged order is by the passage's BEST (minimum) position across the legs
    it appears in; ties break by first appearance (lexical leg before vector),
    which -- with Python's stable sort -- is fully deterministic.  Each merged
    entry carries whichever leg scores it earned (either may be ``None``).
    """

    lexical_pos = {sp.passage_id: i for i, sp in enumerate(lexical)}
    vector_pos = {sp.passage_id: i for i, sp in enumerate(vector)}
    lexical_score = {sp.passage_id: sp.score for sp in lexical}
    vector_score = {sp.passage_id: sp.score for sp in vector}

    ordered_ids = list(
        dict.fromkeys(
            [*(sp.passage_id for sp in lexical), *(sp.passage_id for sp in vector)]
        )
    )
    first_appearance = {pid: i for i, pid in enumerate(ordered_ids)}

    def best_rank(passage_id: UUID) -> int:
        positions = [
            pos
            for pos in (lexical_pos.get(passage_id), vector_pos.get(passage_id))
            if pos is not None
        ]
        return min(positions)

    ordered_ids.sort(key=lambda pid: (best_rank(pid), first_appearance[pid]))
    return tuple(
        _MergedPassage(
            passage_id=pid,
            lexical_score=lexical_score.get(pid),
            vector_score=vector_score.get(pid),
        )
        for pid in ordered_ids
    )


class QueryEmbeddingError(RuntimeError):
    """The injected gateway returned no usable query embedding.

    Raised (never swallowed into a silent lexical fallback) so a hybrid run fails
    closed rather than rank against a fabricated or empty vector."""


class RunRetrieval:
    """Orchestrates one graph-guided hybrid retrieval run against the port."""

    def __init__(
        self,
        port: EvidenceGraphRetrievalPort,
        *,
        gateway: InferenceGateway | None = None,
        lexical_limit: int = DEFAULT_LEXICAL_LIMIT,
        vector_limit: int = DEFAULT_VECTOR_LIMIT,
    ) -> None:
        self._port = port
        self._gateway = gateway
        self._lexical_limit = lexical_limit
        self._vector_limit = vector_limit

    async def execute(
        self,
        request: RetrievalRequest,
        *,
        correlation_id: str,
        task_id: UUID | None = None,
    ) -> RetrievalResult:
        mode = "LEXICAL_ONLY" if self._gateway is None else "HYBRID"
        filters: dict[str, object] = {
            "caseId": str(request.case_id),
            "caseVersion": request.case_version,
            "maxHops": request.max_hops,
            "maxNodes": request.max_nodes,
            "seedCount": len(request.seeds),
            "mode": mode,
        }

        candidates = await self._port.traverse_evidence_graph(
            case_id=request.case_id,
            case_version=request.case_version,
            seeds=request.seeds,
            max_hops=request.max_hops,
            max_nodes=request.max_nodes,
        )
        candidate_ids = tuple(candidate.passage_id for candidate in candidates)

        if not candidate_ids:
            return await self._finish(
                request, filters=filters, task_id=task_id, hits=(), excluded=()
            )

        lexical = await self._port.lexical_search(
            case_id=request.case_id,
            case_version=request.case_version,
            query_text=request.query_text,
            candidate_passage_ids=candidate_ids,
            limit=self._lexical_limit,
        )

        vector: tuple[ScoredPassage, ...] = ()
        if self._gateway is not None:
            embedding = await self._embed_query(request, correlation_id)
            vector = await self._port.vector_search(
                case_id=request.case_id,
                case_version=request.case_version,
                query_embedding=embedding,
                candidate_passage_ids=candidate_ids,
                limit=self._vector_limit,
            )

        merged = _merge_rankings(lexical, vector)
        if not merged:
            return await self._finish(
                request, filters=filters, task_id=task_id, hits=(), excluded=()
            )

        hydrated = await self._port.hydrate_passages(
            case_id=request.case_id,
            case_version=request.case_version,
            passage_ids=tuple(entry.passage_id for entry in merged),
        )
        hydrated_by_id = {passage.passage_id: passage for passage in hydrated}

        surviving: list[RetrievalHit] = []
        excluded: list[ExcludedPassage] = []
        rank = 0
        for entry in merged:
            passage = hydrated_by_id.get(entry.passage_id)
            if passage is None:
                # Reachable at traverse/rank time but gone at hydration -> the
                # source version went stale mid-pipeline; drop it honestly.
                excluded.append(
                    ExcludedPassage(passage_id=entry.passage_id, reason="STALE")
                )
                continue
            if (
                passage.case_id != request.case_id
                or passage.case_version != request.case_version
            ):
                # Defense in depth: the scoped SQL cannot return foreign rows, so
                # this can only fire on a contract breach -- never retrieve it.
                excluded.append(
                    ExcludedPassage(
                        passage_id=entry.passage_id, reason="UNAUTHORIZED_SCOPE"
                    )
                )
                continue
            rank += 1
            surviving.append(
                RetrievalHit(
                    passage_id=passage.passage_id,
                    passage_text=passage.passage_text,
                    document_version_id=passage.document_version_id,
                    page_number=passage.page_number,
                    page_region_id=passage.page_region_id,
                    passage_hash=passage.passage_hash,
                    rank=rank,
                    lexical_score=entry.lexical_score,
                    vector_score=entry.vector_score,
                )
            )

        packed = pack_context(surviving, request.budget_tokens)
        all_excluded = (*excluded, *packed.excluded)
        return await self._finish(
            request,
            filters=filters,
            task_id=task_id,
            hits=packed.included,
            excluded=all_excluded,
            packed_context_vi=packed.packed_context_vi,
            token_estimate=packed.token_estimate,
        )

    async def _finish(
        self,
        request: RetrievalRequest,
        *,
        filters: dict[str, object],
        task_id: UUID | None,
        hits: tuple[RetrievalHit, ...],
        excluded: tuple[ExcludedPassage, ...],
        packed_context_vi: str = "",
        token_estimate: int = 0,
    ) -> RetrievalResult:
        await self._port.persist_query_and_hits(
            case_id=request.case_id,
            case_version=request.case_version,
            task_id=task_id,
            query_text_vi=request.query_text,
            seed_node_refs=request.seeds,
            filters=filters,
            hits=tuple(
                PersistableHit(
                    passage_id=hit.passage_id,
                    rank=hit.rank,
                    lexical_score=hit.lexical_score,
                    vector_score=hit.vector_score,
                    passage_hash=hit.passage_hash,
                )
                for hit in hits
            ),
        )
        return RetrievalResult(
            hits=hits,
            packed_context_vi=packed_context_vi,
            token_estimate=token_estimate,
            excluded=excluded,
        )

    async def _embed_query(
        self, request: RetrievalRequest, correlation_id: str
    ) -> tuple[float, ...]:
        result = await self._gateway.embed(  # type: ignore[union-attr]
            EmbeddingRequest(
                correlation_id=correlation_id,
                case_id=request.case_id,
                texts=[request.query_text],
            )
        )
        return _first_embedding(result.payload)


def _first_embedding(payload: object) -> tuple[float, ...]:
    """Extract the single query vector from an embedding gateway payload.

    The gateway returns one embedding row per input text; we send exactly one
    text, so the vector is ``payload[0]``.  A missing or malformed payload fails
    closed rather than degrade to a fabricated vector."""

    if not isinstance(payload, (list, tuple)) or not payload:
        raise QueryEmbeddingError("embedding gateway returned no vector")
    row = payload[0]
    if not isinstance(row, (list, tuple)) or not row:
        raise QueryEmbeddingError("embedding gateway returned an empty vector")
    try:
        return tuple(float(value) for value in row)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise QueryEmbeddingError("embedding vector is not numeric") from exc
