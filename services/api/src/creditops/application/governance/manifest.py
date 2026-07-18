"""Pure assembly of a :class:`ContextManifest` from what a processor already
loaded (master design section 10.2, P0 #12).

``build_context_manifest`` performs NO IO and reads NO new state: a processor
that has already loaded its scoped evidence view, upstream artifacts, open gaps
and controlled-check results simply hands those refs -- plus the committed goal
contract and its own prompt/schema versions -- to this function, which packs
them into the immutable, hashable snapshot the governance repository persists.

The manifest carries opaque refs ONLY (never inline document text): the id of
every authoritative fact, upstream artifact, open gap/conflict/challenge,
retrieval query and tool result the call was authorized to see, plus the
explicit exclusions and the labelled-synthetic budget inherited from the goal
contract.  ``compute_context_hash`` (domain) turns that content into the stable
``contextHash`` the store deduplicates on.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from creditops.application.governance.contracts import AgentGovernance
from creditops.domain.goal_contracts import (
    AuthorizationSnapshot,
    ContextManifest,
    ExclusionRecord,
    GoalContract,
)


def build_context_manifest(
    *,
    case_id: UUID,
    case_version: int,
    task_id: UUID | None,
    goal_contract: GoalContract,
    agent_role: str,
    profile_version: str,
    prompt_version: str,
    schema_version: str,
    authorization: AuthorizationSnapshot,
    model_version: str | None = None,
    tool_versions: Mapping[str, str] | None = None,
    authoritative_fact_refs: tuple[UUID, ...] = (),
    human_decision_refs: tuple[UUID, ...] = (),
    upstream_artifact_refs: tuple[UUID, ...] = (),
    open_gap_refs: tuple[UUID, ...] = (),
    open_conflict_refs: tuple[UUID, ...] = (),
    open_challenge_refs: tuple[UUID, ...] = (),
    retrieval_query_refs: tuple[UUID, ...] = (),
    tool_result_refs: tuple[UUID, ...] = (),
    explicit_exclusions: tuple[ExclusionRecord, ...] = (),
    manifest_id: UUID | None = None,
    created_at: datetime | None = None,
) -> ContextManifest:
    """Assemble one call's context manifest from already-loaded state.

    The goal contract supplies the manifest's ``goal_contract_id/version`` and
    its budgets, so a manifest can never silently disagree with the contract it
    was bound to.  ``manifest_id`` and ``created_at`` identify the row, not the
    content, and are excluded from ``compute_context_hash``; they default to a
    fresh uuid4 / ``now`` when not supplied.
    """

    return ContextManifest(
        id=manifest_id if manifest_id is not None else uuid4(),
        case_id=case_id,
        case_version=case_version,
        task_id=task_id,
        goal_contract_id=goal_contract.id,
        goal_contract_version=goal_contract.version,
        agent_role=agent_role,
        profile_version=profile_version,
        prompt_version=prompt_version,
        schema_version=schema_version,
        model_version=model_version,
        tool_versions=dict(tool_versions) if tool_versions is not None else {},
        authorization_snapshot=authorization,
        authoritative_fact_refs=authoritative_fact_refs,
        human_decision_refs=human_decision_refs,
        upstream_artifact_refs=upstream_artifact_refs,
        open_gap_refs=open_gap_refs,
        open_conflict_refs=open_conflict_refs,
        open_challenge_refs=open_challenge_refs,
        retrieval_query_refs=retrieval_query_refs,
        tool_result_refs=tool_result_refs,
        explicit_exclusions=explicit_exclusions,
        budgets=goal_contract.budgets,
        created_at=created_at if created_at is not None else datetime.now(UTC),
    )


def manifest_from_governance(
    governance: AgentGovernance,
    *,
    case_id: UUID,
    case_version: int,
    task_id: UUID | None,
    actor_or_service_identity: str,
    case_roles: tuple[str, ...] = (),
    model_version: str | None = None,
    tool_versions: Mapping[str, str] | None = None,
    authoritative_fact_refs: tuple[UUID, ...] = (),
    human_decision_refs: tuple[UUID, ...] = (),
    upstream_artifact_refs: tuple[UUID, ...] = (),
    open_gap_refs: tuple[UUID, ...] = (),
    open_conflict_refs: tuple[UUID, ...] = (),
    open_challenge_refs: tuple[UUID, ...] = (),
    retrieval_query_refs: tuple[UUID, ...] = (),
    tool_result_refs: tuple[UUID, ...] = (),
    explicit_exclusions: tuple[ExclusionRecord, ...] = (),
    manifest_id: UUID | None = None,
    created_at: datetime | None = None,
) -> ContextManifest:
    """Convenience assembly straight from a governance bundle.

    Pulls the agent role, profile version and prompt/schema versions from the
    committed :class:`AgentGovernance` bundle so a processor supplies only the
    per-case refs and its authorization snapshot -- keeping the four processors'
    call sites terse and impossible to desynchronize from the registry.
    """

    return build_context_manifest(
        case_id=case_id,
        case_version=case_version,
        task_id=task_id,
        goal_contract=governance.goal_contract,
        agent_role=governance.agent_role,
        profile_version=governance.profile_version,
        prompt_version=governance.prompt_version,
        schema_version=governance.schema_version,
        authorization=AuthorizationSnapshot(
            actor_or_service_identity=actor_or_service_identity,
            case_roles=case_roles,
        ),
        model_version=model_version,
        tool_versions=tool_versions,
        authoritative_fact_refs=authoritative_fact_refs,
        human_decision_refs=human_decision_refs,
        upstream_artifact_refs=upstream_artifact_refs,
        open_gap_refs=open_gap_refs,
        open_conflict_refs=open_conflict_refs,
        open_challenge_refs=open_challenge_refs,
        retrieval_query_refs=retrieval_query_refs,
        tool_result_refs=tool_result_refs,
        explicit_exclusions=explicit_exclusions,
        manifest_id=manifest_id,
        created_at=created_at,
    )
