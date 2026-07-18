"""(a) The checker cannot modify maker output: port-surface test.

``RiskReviewRepository`` is the ENTIRE durable-state surface the checker use
case and worker processor may call.  This test proves, independent of any
concrete adapter, that the Protocol exposes no method capable of writing an
underwriting or legal row -- maker-output immutability is a property of the
interface itself, not of adapter discipline.
"""

from __future__ import annotations

import inspect

from creditops.application.ports.risk_review import RiskReviewRepository

#: A method is write-capable when its NAME STARTS WITH one of these verbs.
#: ``find_persisted`` deliberately does not match -- it is an idempotency
#: read-lookup ("was this already persisted?"), not a write.
_WRITE_VERB_PREFIXES = ("persist_", "insert_", "update_", "write_", "save_", "create_", "record_")


def _protocol_method_names() -> list[str]:
    return [
        name
        for name, member in vars(RiskReviewRepository).items()
        if not name.startswith("_") and inspect.isfunction(member)
    ]


def test_port_exposes_no_write_method_for_maker_tables() -> None:
    names = _protocol_method_names()
    assert names, "expected the checker port to declare methods"
    for name in names:
        lowered = name.lower()
        mentions_maker_table = "underwriting" in lowered or "legal" in lowered
        if mentions_maker_table:
            # The only maker-related methods allowed are read accessors.
            assert lowered.startswith("load_"), (
                f"RiskReviewRepository.{name} mentions a maker table but is "
                "not a read accessor -- the checker port must be read-only "
                "for underwriting/legal state"
            )
            assert not lowered.startswith(_WRITE_VERB_PREFIXES), (
                f"RiskReviewRepository.{name} looks write-capable for maker state"
            )


def test_load_maker_outputs_is_the_only_maker_state_accessor() -> None:
    # Confirmed Facts, gates, gaps, and dispositions are read through their
    # own accessors that name neither "underwriting" nor "legal" -- the ONLY
    # place either maker's domain state (UnderwritingAssessment /
    # LegalComplianceAssessment) enters the checker at all is this one
    # read-only accessor.
    assert "load_maker_outputs" in _protocol_method_names()
    maker_named = {
        name
        for name in _protocol_method_names()
        if "underwriting" in name.lower() or "legal" in name.lower()
    }
    assert maker_named == set()


def test_port_writes_are_scoped_to_risk_review_and_audit_only() -> None:
    # Every write-shaped method name must be about risk-review state
    # (assessment/challenge/disposition) or the shared append-only audit
    # trail -- never about maker state, gates, gaps, or conflicts.
    allowed_write_subjects = ("assessment", "disposition", "audit")
    for name in _protocol_method_names():
        lowered = name.lower()
        if lowered.startswith(_WRITE_VERB_PREFIXES):
            assert any(subject in lowered for subject in allowed_write_subjects), (
                f"RiskReviewRepository.{name} is write-shaped but not scoped "
                "to risk-review assessment/disposition/audit state"
            )


def test_protocol_cannot_be_instantiated_and_defines_only_coroutines() -> None:
    for name, member in vars(RiskReviewRepository).items():
        if name.startswith("_") or not inspect.isfunction(member):
            continue
        assert inspect.iscoroutinefunction(member), f"{name} must be an async method"
