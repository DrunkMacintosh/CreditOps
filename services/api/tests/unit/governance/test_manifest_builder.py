"""Context-manifest builder (master design section 10.2, P0 #12).

Pure assembly: the builder inherits the goal contract's id/version and budgets,
carries only opaque refs, and produces a manifest whose ``compute_context_hash``
is deterministic for identical content and shifts when the authorized context
(refs or exclusions) changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from creditops.application.governance import (
    build_context_manifest,
    governance_for,
    manifest_from_governance,
)
from creditops.domain.goal_contracts import (
    AuthorizationSnapshot,
    ExclusionRecord,
    compute_context_hash,
)
from creditops.domain.orchestration import TaskType

_UNDERWRITING = governance_for(TaskType.CREDIT_UNDERWRITING)


def _manifest(**overrides: object):
    fields: dict[str, object] = {
        "case_id": uuid4(),
        "case_version": 3,
        "task_id": uuid4(),
        "actor_or_service_identity": "service:agent-worker",
        "case_roles": ("CREDIT_UNDERWRITING",),
    }
    fields.update(overrides)
    return manifest_from_governance(_UNDERWRITING, **fields)  # type: ignore[arg-type]


def test_manifest_inherits_contract_identity_and_budgets() -> None:
    contract = _UNDERWRITING.goal_contract
    manifest = _manifest()

    assert manifest.goal_contract_id == contract.id
    assert manifest.goal_contract_version == contract.version
    assert manifest.budgets == contract.budgets
    assert manifest.agent_role == _UNDERWRITING.agent_role
    assert manifest.profile_version == _UNDERWRITING.profile_version
    assert manifest.prompt_version == _UNDERWRITING.prompt_version
    assert manifest.schema_version == _UNDERWRITING.schema_version


def test_hash_is_deterministic_for_identical_content() -> None:
    fact_a, fact_b = uuid4(), uuid4()
    case_id, task_id = uuid4(), uuid4()
    base = _manifest(
        case_id=case_id,
        task_id=task_id,
        authoritative_fact_refs=(fact_a, fact_b),
    )
    # A distinct row (fresh id + created_at) with the SAME content hashes
    # identically -- the surrogate id and wall-clock are excluded.
    twin = _manifest(
        case_id=case_id,
        task_id=task_id,
        authoritative_fact_refs=(fact_b, fact_a),  # order is not material
        manifest_id=uuid4(),
        created_at=datetime(2031, 1, 1, tzinfo=UTC),
    )

    assert compute_context_hash(base) == compute_context_hash(twin)


def test_hash_changes_when_the_authorized_refs_change() -> None:
    case_id, task_id = uuid4(), uuid4()
    baseline = compute_context_hash(
        _manifest(case_id=case_id, task_id=task_id, open_gap_refs=())
    )
    with_gap = compute_context_hash(
        _manifest(case_id=case_id, task_id=task_id, open_gap_refs=(uuid4(),))
    )

    assert with_gap != baseline


def test_explicit_exclusions_are_recorded_and_change_the_hash() -> None:
    case_id, task_id = uuid4(), uuid4()
    excluded = uuid4()
    base = _manifest(case_id=case_id, task_id=task_id)
    with_exclusion = _manifest(
        case_id=case_id,
        task_id=task_id,
        explicit_exclusions=(ExclusionRecord(ref=excluded, reason="STALE"),),
    )

    assert with_exclusion.explicit_exclusions[0].ref == excluded
    assert with_exclusion.explicit_exclusions[0].reason == "STALE"
    assert compute_context_hash(with_exclusion) != compute_context_hash(base)


def test_refs_and_tool_versions_are_carried_through() -> None:
    fact = uuid4()
    tool_result = uuid4()
    manifest = _manifest(
        authoritative_fact_refs=(fact,),
        tool_result_refs=(tool_result,),
        tool_versions={"kyc": "v3"},
    )

    assert manifest.authoritative_fact_refs == (fact,)
    assert manifest.tool_result_refs == (tool_result,)
    assert dict(manifest.tool_versions) == {"kyc": "v3"}


def test_build_context_manifest_defaults_row_identity() -> None:
    # Called without an explicit id/created_at, two builds still differ only in
    # row identity (fresh id + created_at) and therefore hash identically.
    contract = _UNDERWRITING.goal_contract
    common = {
        "case_id": uuid4(),
        "case_version": 1,
        "task_id": uuid4(),
        "goal_contract": contract,
        "agent_role": "CREDIT_UNDERWRITING",
        "profile_version": "underwriting-profile-v1",
        "prompt_version": "underwriting-prompt-v1",
        "schema_version": "underwriting-assessment-v1",
        "authorization": AuthorizationSnapshot(
            actor_or_service_identity="service:agent-worker",
            case_roles=("CREDIT_UNDERWRITING",),
        ),
    }
    first = build_context_manifest(**common)  # type: ignore[arg-type]
    second = build_context_manifest(**common)  # type: ignore[arg-type]

    assert first.id != second.id
    assert compute_context_hash(first) == compute_context_hash(second)
