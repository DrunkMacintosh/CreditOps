"""Pure retrieval-domain tests (domain/retrieval.py): deterministic token
packing, budget edge cases, and the citation validator's rejections.

All identifiers are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from uuid import UUID

from creditops.domain.retrieval import (
    Citation,
    RetrievalHit,
    estimate_tokens,
    pack_context,
    validate_citations,
)

DOC = UUID("61000000-0000-0000-0000-0000000000f1")
REGION = UUID("62000000-0000-0000-0000-0000000000f1")
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _hit(
    suffix: int,
    text: str,
    rank: int,
    *,
    lexical: float | None = 0.5,
    vector: float | None = None,
    passage_hash: str = HASH_A,
) -> RetrievalHit:
    return RetrievalHit(
        passage_id=UUID(f"63000000-0000-0000-0000-0000000000{suffix:02x}"),
        passage_text=text,
        document_version_id=DOC,
        page_number=1,
        page_region_id=REGION,
        passage_hash=passage_hash,
        rank=rank,
        lexical_score=lexical,
        vector_score=vector,
    )


# -- estimate_tokens ----------------------------------------------------------


def test_estimate_tokens_is_ceil_chars_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("A" * 4) == 1
    assert estimate_tokens("A" * 5) == 2  # ceil(5/4)
    assert estimate_tokens("A" * 8) == 2
    assert estimate_tokens("A" * 9) == 3  # ceil(9/4)


# -- pack_context -------------------------------------------------------------


def test_pack_includes_everything_within_budget() -> None:
    hits = (_hit(1, "A" * 4, 1), _hit(2, "B" * 4, 2))
    packed = pack_context(hits, budget_tokens=10)

    assert packed.included == hits
    assert packed.excluded == ()
    assert packed.token_estimate == 2  # 1 + 1
    assert packed.packed_context_vi == "AAAA\n\nBBBB"


def test_pack_excludes_the_overflowing_hit_as_out_of_budget() -> None:
    big = _hit(1, "A" * 40, 1)  # 10 tokens, exactly the budget
    small = _hit(2, "B" * 4, 2)  # 1 token, no longer fits
    packed = pack_context((big, small), budget_tokens=10)

    assert packed.included == (big,)
    assert packed.token_estimate == 10
    assert len(packed.excluded) == 1
    assert packed.excluded[0].passage_id == small.passage_id
    assert packed.excluded[0].reason == "OUT_OF_BUDGET"


def test_pack_is_prefix_stops_after_first_overflow() -> None:
    first = _hit(1, "A" * 40, 1)  # 10 tokens -> fills the budget
    overflow = _hit(2, "B" * 80, 2)  # 20 tokens -> overflows
    would_fit = _hit(3, "C" * 4, 3)  # 1 token, but the pack already overflowed
    packed = pack_context((first, overflow, would_fit), budget_tokens=10)

    assert packed.included == (first,)
    assert [ex.passage_id for ex in packed.excluded] == [
        overflow.passage_id,
        would_fit.passage_id,
    ]
    assert all(ex.reason == "OUT_OF_BUDGET" for ex in packed.excluded)


def test_pack_excludes_a_single_oversized_hit() -> None:
    oversized = _hit(1, "A" * 44, 1)  # 11 tokens > budget 10
    packed = pack_context((oversized,), budget_tokens=10)

    assert packed.included == ()
    assert packed.packed_context_vi == ""
    assert packed.token_estimate == 0
    assert packed.excluded[0].reason == "OUT_OF_BUDGET"


def test_pack_of_no_hits_is_empty_and_honest() -> None:
    packed = pack_context((), budget_tokens=10)

    assert packed.included == ()
    assert packed.excluded == ()
    assert packed.packed_context_vi == ""
    assert packed.token_estimate == 0


def test_pack_is_deterministic() -> None:
    hits = (_hit(1, "A" * 20, 1), _hit(2, "B" * 24, 2), _hit(3, "C" * 4, 3))
    first = pack_context(hits, budget_tokens=8)
    second = pack_context(hits, budget_tokens=8)

    assert first == second


# -- validate_citations -------------------------------------------------------


def test_passage_citation_accepted_only_when_hash_is_retrieved() -> None:
    result = validate_citations(
        [Citation(kind="PASSAGE", value=HASH_A), Citation(kind="PASSAGE", value=HASH_B)],
        allowed_passage_hashes=[HASH_A],
        allowed_fact_ids=[],
    )

    assert [c.value for c in result.accepted] == [HASH_A]
    assert [c.value for c in result.rejected] == [HASH_B]
    assert result.is_valid is False


def test_confirmed_fact_citation_gated_by_authorized_ids() -> None:
    fact_id = "63000000-0000-0000-0000-0000000000f1"
    result = validate_citations(
        [
            Citation(kind="CONFIRMED_FACT", value=fact_id),
            Citation(kind="CONFIRMED_FACT", value="00000000-0000-0000-0000-000000000000"),
        ],
        allowed_passage_hashes=[],
        allowed_fact_ids=[fact_id],
    )

    assert [c.value for c in result.accepted] == [fact_id]
    assert len(result.rejected) == 1
    assert result.is_valid is False


def test_all_supported_citations_are_valid() -> None:
    result = validate_citations(
        [Citation(kind="PASSAGE", value=HASH_C)],
        allowed_passage_hashes=[HASH_A, HASH_C],
        allowed_fact_ids=[],
    )

    assert result.rejected == ()
    assert result.is_valid is True


def test_no_citations_is_vacuously_valid() -> None:
    result = validate_citations(
        [], allowed_passage_hashes=[HASH_A], allowed_fact_ids=["x"]
    )

    assert result.accepted == ()
    assert result.rejected == ()
    assert result.is_valid is True


def test_passage_hash_not_matched_against_fact_ids() -> None:
    # A PASSAGE citation whose value happens to equal an allowed FACT id is still
    # rejected -- the kinds are validated against their own authorized sets.
    shared = "a" * 64
    result = validate_citations(
        [Citation(kind="PASSAGE", value=shared)],
        allowed_passage_hashes=[],
        allowed_fact_ids=[shared],
    )

    assert result.rejected[0].value == shared
    assert result.is_valid is False
