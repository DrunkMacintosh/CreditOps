"""The committed goal-contract registry (master design section 10.1, P0 #12).

Every contract must be structurally valid, restate the universal human-only
bans, and -- critically -- its prompt/schema versions must equal the LIVE
processor constants.  The registry hand-copies those version literals precisely
so a processor prompt/schema bump that is not mirrored here fails this suite
(drift is a test failure, never a silent mismatch).
"""

from __future__ import annotations

from uuid import UUID

import pytest

from creditops.application.credit_ops.assembler import (
    CREDIT_OPS_PROMPT_VERSION,
    CREDIT_OPS_SCHEMA_VERSION,
)
from creditops.application.governance import (
    all_goal_contracts,
    goal_contract_for,
    governance_by_key,
    governance_for,
)
from creditops.application.governance.contracts import (
    RISK_REVIEW_PRE_ANALYSIS_CONTRACT_KEY,
)
from creditops.application.legal.reviewer import (
    LEGAL_PROMPT_VERSION,
    LEGAL_SCHEMA_VERSION,
)
from creditops.application.risk_review.checker import (
    PRE_ANALYSIS_PROMPT_VERSION,
    PRE_ANALYSIS_SCHEMA_VERSION,
    RISK_REVIEW_PROMPT_VERSION,
    RISK_REVIEW_SCHEMA_VERSION,
)
from creditops.application.underwriting.maker import (
    UNDERWRITING_PROMPT_VERSION,
    UNDERWRITING_SCHEMA_VERSION,
)
from creditops.domain.goal_contracts import UNIVERSAL_PROHIBITED_ACTIONS
from creditops.domain.orchestration import TaskType


def test_every_registry_contract_is_valid_and_restates_universal_bans() -> None:
    contracts = all_goal_contracts()
    # The complete committed set: the four specialists, the blind Pass A
    # pre-analysis, the orchestrator, and document ingestion.
    assert len(contracts) == 7
    for contract in contracts:
        # The domain model already forbids allowed/prohibited overlap and an
        # empty prohibition set; assert the governance-level guarantees.
        assert UNIVERSAL_PROHIBITED_ACTIONS.issubset(set(contract.prohibited_actions))
        assert contract.allowed_actions  # every agent may do SOMETHING
        assert not (set(contract.allowed_actions) & set(contract.prohibited_actions))
        assert contract.objective_vi.strip()
        assert contract.budgets.max_input_tokens > 0
        assert contract.budgets.max_output_tokens > 0
        assert contract.budgets.max_tool_calls > 0


def test_contract_ids_are_deterministic_and_unique() -> None:
    # Stable ids mean a manifest's goal_contract_id equals the seeded row's id
    # with no bootstrap ordering.
    assert goal_contract_for(TaskType.CREDIT_UNDERWRITING).id == goal_contract_for(
        TaskType.CREDIT_UNDERWRITING
    ).id
    ids = [contract.id for contract in all_goal_contracts()]
    assert all(isinstance(contract_id, UUID) for contract_id in ids)
    assert len(set(ids)) == len(ids)
    keys = {(c.contract_key, c.version) for c in all_goal_contracts()}
    assert len(keys) == len(all_goal_contracts())


@pytest.mark.parametrize(
    ("task_type", "prompt_version", "schema_version"),
    [
        (
            TaskType.CREDIT_UNDERWRITING,
            UNDERWRITING_PROMPT_VERSION,
            UNDERWRITING_SCHEMA_VERSION,
        ),
        (
            TaskType.LEGAL_COMPLIANCE_COLLATERAL,
            LEGAL_PROMPT_VERSION,
            LEGAL_SCHEMA_VERSION,
        ),
        (
            TaskType.INDEPENDENT_RISK_REVIEW,
            RISK_REVIEW_PROMPT_VERSION,
            RISK_REVIEW_SCHEMA_VERSION,
        ),
        (
            TaskType.CREDIT_OPERATIONS,
            CREDIT_OPS_PROMPT_VERSION,
            CREDIT_OPS_SCHEMA_VERSION,
        ),
    ],
)
def test_specialist_versions_match_live_processor_constants(
    task_type: TaskType, prompt_version: str, schema_version: str
) -> None:
    bundle = governance_for(task_type)
    assert bundle.prompt_version == prompt_version
    assert bundle.schema_version == schema_version
    # The output schema the contract binds also tracks the live schema version.
    assert bundle.goal_contract.output_schema_version == schema_version


def test_blind_pre_analysis_contract_tracks_the_pass_a_constants() -> None:
    # The Independent Risk Review's blind Pass A is a distinct model call with
    # its OWN contract, reached by key rather than by task type.
    bundle = governance_by_key(RISK_REVIEW_PRE_ANALYSIS_CONTRACT_KEY)
    assert bundle.task_type is TaskType.INDEPENDENT_RISK_REVIEW
    assert bundle.prompt_version == PRE_ANALYSIS_PROMPT_VERSION
    assert bundle.schema_version == PRE_ANALYSIS_SCHEMA_VERSION
    assert bundle.goal_contract.output_schema_version == PRE_ANALYSIS_SCHEMA_VERSION
    # It never sees a maker artifact -- its allowed set names only blind reads.
    assert "READ_MAKER_OUTPUTS" not in bundle.goal_contract.allowed_actions
    # And it is the distinct Pass B contract's sibling, not the same row.
    assert bundle.goal_contract.id != governance_for(
        TaskType.INDEPENDENT_RISK_REVIEW
    ).goal_contract.id


def test_every_task_type_maps_to_a_contract() -> None:
    for task_type in TaskType:
        assert goal_contract_for(task_type).version >= 1
    # Risk review's primary (task-type) contract is the Pass B checker one.
    assert (
        goal_contract_for(TaskType.INDEPENDENT_RISK_REVIEW).contract_key
        == "risk-review-assessment"
    )


def test_registry_lookup_fails_closed_on_an_unknown_key() -> None:
    # The lookup raises rather than returning a silent default -- the caller
    # treats a miss as manual review.
    with pytest.raises(KeyError):
        governance_by_key("no-such-contract")


def test_profile_versions_are_distinct_per_agent() -> None:
    profiles = [
        governance_for(TaskType.CREDIT_UNDERWRITING).profile_version,
        governance_for(TaskType.LEGAL_COMPLIANCE_COLLATERAL).profile_version,
        governance_for(TaskType.INDEPENDENT_RISK_REVIEW).profile_version,
        governance_by_key(RISK_REVIEW_PRE_ANALYSIS_CONTRACT_KEY).profile_version,
        governance_for(TaskType.CREDIT_OPERATIONS).profile_version,
    ]
    assert len(set(profiles)) == len(profiles)
