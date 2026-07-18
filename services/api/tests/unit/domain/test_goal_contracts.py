from datetime import UTC, datetime
from typing import get_args
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.goal_contracts import (
    UNIVERSAL_PROHIBITED_ACTIONS,
    AuthorizationSnapshot,
    BudgetSpec,
    ContextManifest,
    ExclusionReason,
    ExclusionRecord,
    GoalContract,
    compute_context_hash,
)

BUDGET = BudgetSpec(max_input_tokens=100_000, max_output_tokens=8_000, max_tool_calls=12)
CREATED_AT = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


def goal_contract(**overrides: object) -> GoalContract:
    fields: dict[str, object] = {
        "id": uuid4(),
        "contract_key": "underwriting-assessment",
        "version": 1,
        "objective_vi": "Đánh giá thẩm định tín dụng của khách hàng doanh nghiệp",
        "allowed_actions": ("READ_EVIDENCE", "RUN_DETERMINISTIC_CALCULATOR"),
        "prohibited_actions": tuple(sorted(UNIVERSAL_PROHIBITED_ACTIONS)),
        "success_conditions_vi": ("Đủ chỉ số tài chính có nguồn dẫn",),
        "required_evidence_kinds": ("FINANCIAL_STATEMENT",),
        "output_schema_ref": "underwriting-assessment-output",
        "output_schema_version": "1",
        "required_human_gate": "HG_UNDERWRITING_ASSESSMENT_REVIEWED",
        "budgets": BUDGET,
    }
    fields.update(overrides)
    return GoalContract(**fields)  # type: ignore[arg-type]


def context_manifest(**overrides: object) -> ContextManifest:
    fields: dict[str, object] = {
        "id": uuid4(),
        "case_id": uuid4(),
        "case_version": 3,
        "task_id": uuid4(),
        "goal_contract_id": uuid4(),
        "goal_contract_version": 1,
        "agent_role": "CREDIT_UNDERWRITING",
        "profile_version": "underwriting-profile-v1",
        "prompt_version": "underwriting-prompt-v1",
        "schema_version": "1",
        "model_version": "fpt-reasoning-v1",
        "tool_versions": {"ratio_calculator": "1", "reconciliation": "2"},
        "authorization_snapshot": AuthorizationSnapshot(
            actor_or_service_identity="service:agent-worker",
            case_roles=("UNDERWRITER",),
        ),
        "budgets": BUDGET,
        "created_at": CREATED_AT,
    }
    fields.update(overrides)
    return ContextManifest(**fields)  # type: ignore[arg-type]


def test_valid_goal_contract_binds_objective_actions_and_budget() -> None:
    contract = goal_contract()

    assert contract.version == 1
    assert UNIVERSAL_PROHIBITED_ACTIONS.issubset(set(contract.prohibited_actions))
    assert contract.budgets.max_tool_calls == 12


def test_goal_contract_missing_a_universal_ban_is_rejected() -> None:
    # A contract that names every universal ban EXCEPT APPROVE_CREDIT must fail:
    # the human-only credit-approval authority can never be omitted.
    incomplete = tuple(sorted(UNIVERSAL_PROHIBITED_ACTIONS - {"APPROVE_CREDIT"}))

    with pytest.raises(ValidationError, match="APPROVE_CREDIT"):
        goal_contract(prohibited_actions=incomplete)


def test_goal_contract_rejects_action_that_is_both_allowed_and_prohibited() -> None:
    with pytest.raises(ValidationError, match="both allowed and prohibited"):
        goal_contract(
            allowed_actions=("READ_EVIDENCE", "WAIVE_POLICY"),
            prohibited_actions=tuple(sorted(UNIVERSAL_PROHIBITED_ACTIONS)),
        )


def test_goal_contract_requires_a_nonempty_prohibition_set() -> None:
    with pytest.raises(ValidationError):
        goal_contract(prohibited_actions=())


def test_goal_contract_is_immutable() -> None:
    contract = goal_contract()

    with pytest.raises(ValidationError, match="frozen"):
        contract.version = 2  # type: ignore[misc]


def test_budget_bounds_must_be_strictly_positive() -> None:
    with pytest.raises(ValidationError):
        BudgetSpec(max_input_tokens=0, max_output_tokens=8_000, max_tool_calls=12)


def test_context_hash_is_deterministic_for_identical_content() -> None:
    base = context_manifest(authoritative_fact_refs=(uuid4(), uuid4()))

    # Same object hashes identically, and a distinct row that differs ONLY in
    # the excluded surrogate id / wall-clock created_at hashes identically too.
    rerow = base.model_copy(update={"id": uuid4(), "created_at": datetime(2030, 1, 1, tzinfo=UTC)})

    assert compute_context_hash(base) == compute_context_hash(base)
    assert compute_context_hash(base) == compute_context_hash(rerow)


def test_context_hash_ignores_input_ordering_of_ref_tuples() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    d, e = uuid4(), uuid4()

    ordered = context_manifest(
        authoritative_fact_refs=(a, b, c),
        human_decision_refs=(d, e),
    )
    # Same refs, supplied in a different order, plus a fresh row identity.
    shuffled = ordered.model_copy(
        update={
            "id": uuid4(),
            "created_at": datetime(2030, 1, 1, tzinfo=UTC),
            "authoritative_fact_refs": (c, a, b),
            "human_decision_refs": (e, d),
        }
    )

    assert compute_context_hash(ordered) == compute_context_hash(shuffled)


def test_context_hash_changes_when_a_material_field_changes() -> None:
    base = context_manifest()
    baseline = compute_context_hash(base)

    changed_role = base.model_copy(update={"agent_role": "INDEPENDENT_RISK_REVIEW"})
    added_ref = base.model_copy(update={"open_gap_refs": (uuid4(),)})

    assert compute_context_hash(changed_role) != baseline
    assert compute_context_hash(added_ref) != baseline


def test_context_hash_changes_when_an_exclusion_is_added() -> None:
    base = context_manifest()
    with_exclusion = base.model_copy(
        update={"explicit_exclusions": (ExclusionRecord(ref=uuid4(), reason="STALE"),)}
    )

    assert compute_context_hash(with_exclusion) != compute_context_hash(base)


def test_exclusion_reasons_are_a_closed_set() -> None:
    assert set(get_args(ExclusionReason)) == {
        "STALE",
        "UNAUTHORIZED",
        "SUPERSEDED",
        "OUTSIDE_BUDGET",
    }
    for reason in get_args(ExclusionReason):
        assert ExclusionRecord(ref=uuid4(), reason=reason).reason == reason

    with pytest.raises(ValidationError):
        ExclusionRecord(ref=uuid4(), reason="ARCHIVED")  # type: ignore[arg-type]


def test_manifest_ref_lists_hold_only_uuids() -> None:
    manifest = context_manifest(authoritative_fact_refs=(uuid4(),))

    assert all(isinstance(ref, UUID) for ref in manifest.authoritative_fact_refs)
