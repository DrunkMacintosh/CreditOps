"""Pure domain contracts and functions for graph-guided hybrid retrieval
(master design sections 12, 12.2, 12.3).

Nothing in this module touches durable state, a provider, or a clock: it defines
the frozen value objects the retrieval pipeline speaks in and the two pure
algorithms the pipeline delegates to --

* ``pack_context`` -- deterministic, greedy, rank-ordered token packing to a
  budget, recording every passage it drops as ``OUT_OF_BUDGET``; and
* ``validate_citations`` -- rejecting any claim citation whose passage hash or
  confirmed-fact id is not in the authorized retrieved set.

The token estimate is the documented heuristic ``ceil(len(text) / 4)`` characters
-> tokens; it is an ESTIMATE for budgeting only and never claims to be an exact
provider token count.  Retrieval returns ORIGINAL passages and their immutable
source hashes; it can never confirm a fact, satisfy a gate, or record a decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: The four typed seed-node kinds a retrieval traversal may start from
#: (Case Evidence Graph entry points; master design section 12.2).
SeedNodeKind = Literal["CONFIRMED_FACT", "DOCUMENT_VERSION", "GAP", "CONFLICT"]

#: Why a candidate passage was excluded from the packed context.  Every
#: exclusion is recorded -- retrieval never silently drops evidence.
ExclusionReason = Literal["STALE", "OUT_OF_BUDGET", "UNAUTHORIZED_SCOPE"]

#: What a claim citation points at: an original passage (by immutable hash) or a
#: confirmed fact (by id).  A retrieved/authorized set gates both.
CitationKind = Literal["PASSAGE", "CONFIRMED_FACT"]

#: Bounds on graph traversal (master design section 12.2: "allowed edge
#: types/hops/node count").  Kept as constants so request validation and the
#: adapter agree on the ceiling.
MAX_HOPS_CEILING = 3
MAX_NODES_CEILING = 200

#: Documented token-estimate heuristic: characters per estimated token.
CHARS_PER_TOKEN = 4


class SeedNodeRef(BaseModel):
    """One typed seed node the traversal starts from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SeedNodeKind
    node_id: UUID


class RetrievalRequest(BaseModel):
    """An authorized, case+version-scoped retrieval request.

    ``max_hops``/``max_nodes`` are hard-capped at the section 12.2 ceilings;
    ``budget_tokens`` is the packing budget the greedy packer honours.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: UUID
    case_version: int = Field(ge=1)
    query_text: str = Field(min_length=1, max_length=8_000)
    seeds: tuple[SeedNodeRef, ...] = ()
    max_hops: int = Field(ge=1, le=MAX_HOPS_CEILING)
    max_nodes: int = Field(ge=1, le=MAX_NODES_CEILING)
    budget_tokens: int = Field(ge=1)


class RetrievalHit(BaseModel):
    """One hydrated hit: an ORIGINAL passage with its immutable source refs, the
    sha256 passage hash a citation is checked against, and the merge scores."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passage_id: UUID
    passage_text: str = Field(min_length=1)
    document_version_id: UUID
    page_number: int | None = Field(default=None, ge=1)
    page_region_id: UUID | None = None
    passage_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rank: int = Field(ge=1)
    lexical_score: float | None = None
    vector_score: float | None = None


class ExcludedPassage(BaseModel):
    """A passage the pipeline reached but did not deliver, with the honest
    reason (stale, out of budget, or -- defensively -- out of authorized scope)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passage_id: UUID
    reason: ExclusionReason


class RetrievalResult(BaseModel):
    """The full outcome of one retrieval run: the delivered hits in rank order,
    the packed Vietnamese context string, its token estimate, and every
    exclusion.  A run that reaches no passage returns an honest empty result --
    empty hits, empty ``packed_context_vi`` -- never a fabricated context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hits: tuple[RetrievalHit, ...] = ()
    packed_context_vi: str = ""
    token_estimate: int = Field(default=0, ge=0)
    excluded: tuple[ExcludedPassage, ...] = ()


class PackedContext(BaseModel):
    """Result of ``pack_context``: the prefix of hits that fit the budget, the
    ``OUT_OF_BUDGET`` remainder, the packed string and its token estimate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    included: tuple[RetrievalHit, ...]
    excluded: tuple[ExcludedPassage, ...]
    packed_context_vi: str
    token_estimate: int = Field(ge=0)


class Citation(BaseModel):
    """One citation a downstream claim makes: a passage hash or a confirmed-fact
    id.  ``value`` is the 64-hex passage hash for ``PASSAGE`` and the fact id
    string for ``CONFIRMED_FACT``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CitationKind
    value: str = Field(min_length=1)


class CitationValidation(BaseModel):
    """Result of ``validate_citations``: the accepted citations, the rejected
    ones (each unsupported by the retrieved/authorized set), and ``is_valid``
    (no rejections)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: tuple[Citation, ...]
    rejected: tuple[Citation, ...]
    is_valid: bool


#: The delimiter between packed passages in ``packed_context_vi``.  It is NOT
#: counted against the budget: the budget is measured on passage text alone so a
#: hit's cost is stable regardless of its position.
_PACK_DELIMITER = "\n\n"


def estimate_tokens(text: str) -> int:
    """Estimate tokens for ``text`` as ``ceil(len(text) / CHARS_PER_TOKEN)``.

    Documented heuristic only -- deterministic and provider-agnostic.  The empty
    string costs 0; any non-empty passage costs at least 1.
    """

    length = len(text)
    if length == 0:
        return 0
    return (length + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def pack_context(
    hits: Sequence[RetrievalHit], budget_tokens: int
) -> PackedContext:
    """Greedily pack ``hits`` (already in rank order) until the token budget.

    Prefix packing: hits are taken in order while the running estimate stays
    within ``budget_tokens``; the first hit that would overflow -- and every hit
    after it -- is recorded as ``OUT_OF_BUDGET``.  This is fully deterministic
    (input order is the only tie-break) and honest: a hit is never silently
    dropped, and a single oversized hit excludes itself and the tail.
    """

    included: list[RetrievalHit] = []
    excluded: list[ExcludedPassage] = []
    total = 0
    overflowed = False
    for hit in hits:
        cost = estimate_tokens(hit.passage_text)
        if overflowed or total + cost > budget_tokens:
            overflowed = True
            excluded.append(
                ExcludedPassage(passage_id=hit.passage_id, reason="OUT_OF_BUDGET")
            )
            continue
        total += cost
        included.append(hit)
    packed = _PACK_DELIMITER.join(hit.passage_text for hit in included)
    return PackedContext(
        included=tuple(included),
        excluded=tuple(excluded),
        packed_context_vi=packed,
        token_estimate=total,
    )


def validate_citations(
    claim_citations: Iterable[Citation],
    *,
    allowed_passage_hashes: Iterable[str],
    allowed_fact_ids: Iterable[str],
) -> CitationValidation:
    """Reject any claim citation not backed by the retrieved/authorized set.

    A ``PASSAGE`` citation is accepted iff its hash is in
    ``allowed_passage_hashes`` (the hashes of the passages this run actually
    returned); a ``CONFIRMED_FACT`` citation is accepted iff its id is in
    ``allowed_fact_ids`` (the case-scoped confirmed facts in context).  Every
    other citation is rejected -- confidence never substitutes for evidence, and
    a citation the retrieval did not support cannot enter a material conclusion.
    """

    passage_hashes = frozenset(allowed_passage_hashes)
    fact_ids = frozenset(allowed_fact_ids)
    accepted: list[Citation] = []
    rejected: list[Citation] = []
    for citation in claim_citations:
        if citation.kind == "PASSAGE":
            allowed = citation.value in passage_hashes
        else:
            allowed = citation.value in fact_ids
        (accepted if allowed else rejected).append(citation)
    return CitationValidation(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        is_valid=not rejected,
    )
