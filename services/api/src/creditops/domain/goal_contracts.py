"""Agent-governance domain models: GoalContract and ContextManifest.

Master design (docs/superpowers/specs/2026-07-18-full-credit-lifecycle-agent-
workflow-design.md sections 10.1 Goal hierarchy, 10.2 ContextManifest, 13
Domain model): every model call is bounded by an immutable, versioned
GoalContract and a persisted, hashable ContextManifest.  These are the two
schema foundations for P0 #12.

Invariants enforced here (not merely documented):

- A GoalContract names BOTH what an execution may do and -- non-negotiably --
  what it may NOT do.  ``prohibited_actions`` must be a superset of
  ``UNIVERSAL_PROHIBITED_ACTIONS``: the human-only authorities of master
  design section 3.2 (approve/reject credit, waive policy, sign, disburse,
  contact the customer, confirm a candidate fact, close a gap/conflict, expand
  its own permissions) can never be delegated to any agent, so every contract
  must restate them.  An action may never appear in both the allowed and the
  prohibited set.
- A ContextManifest is the exact, ordered snapshot of everything one model
  call was authorized to see (section 10.2).  ``compute_context_hash`` derives
  a deterministic SHA-256 over the canonical content -- excluding the
  surrogate ``id`` and wall-clock ``created_at`` -- with sorted object keys and
  sorted ref lists, so two manifests with identical content hash identically
  regardless of the order refs were supplied in.  ``id`` and ``created_at`` are
  excluded precisely so the hash identifies *content*, not a row.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId, TaskId

type GoalContractId = UUID
type ContextManifestId = UUID

#: The human-only authorities of master design section 3.2.  No agent may ever
#: hold these, so EVERY goal contract must restate them in
#: ``prohibited_actions``; the model validator rejects any contract whose
#: prohibitions are not a superset of this set.
UNIVERSAL_PROHIBITED_ACTIONS: frozenset[str] = frozenset(
    {
        "APPROVE_CREDIT",
        "REJECT_CREDIT",
        "WAIVE_POLICY",
        "SIGN_DOCUMENT",
        "EXECUTE_DISBURSEMENT",
        "SEND_CUSTOMER_COMMUNICATION",
        "CONFIRM_CANDIDATE_FACT",
        "CLOSE_GAP_OR_CONFLICT",
        "EXPAND_OWN_PERMISSIONS",
    }
)

#: The closed set of reasons a manifest may cite for excluding otherwise
#: in-scope material (master design section 10.2 "explicit exclusions").
ExclusionReason = Literal["STALE", "UNAUTHORIZED", "SUPERSEDED", "OUTSIDE_BUDGET"]


class BudgetSpec(BaseModel):
    """A layered token/tool budget (master design section 12.3).  Every bound
    is a hard positive ceiling; a zero or negative budget is a construction
    error."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)


class GoalContract(BaseModel):
    """An immutable, versioned goal contract (master design section 10.1).

    Bounds one agent execution: its objective, the actions it may and may not
    take, the evidence and success conditions that define done, the output
    schema it must satisfy, an optional required human gate, and its budget.
    An agent can never widen its own goal -- the contract is append-only in the
    store and frozen here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: GoalContractId
    contract_key: str = Field(min_length=1)
    version: int = Field(ge=1)
    objective_vi: str = Field(min_length=1)
    allowed_actions: tuple[str, ...] = ()
    prohibited_actions: tuple[str, ...] = Field(min_length=1)
    success_conditions_vi: tuple[str, ...] = ()
    required_evidence_kinds: tuple[str, ...] = ()
    output_schema_ref: str = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)
    required_human_gate: str | None = None
    budgets: BudgetSpec

    @model_validator(mode="after")
    def _actions_are_disjoint_and_restate_universal_bans(self) -> Self:
        allowed = set(self.allowed_actions)
        prohibited = set(self.prohibited_actions)
        overlap = allowed & prohibited
        if overlap:
            raise ValueError(
                "an action cannot be both allowed and prohibited: " + ", ".join(sorted(overlap))
            )
        missing = UNIVERSAL_PROHIBITED_ACTIONS - prohibited
        if missing:
            raise ValueError(
                "prohibited_actions must restate every universal ban; missing: "
                + ", ".join(sorted(missing))
            )
        return self


class AuthorizationSnapshot(BaseModel):
    """The actor/service identity and case roles under which a manifest was
    built (master design section 10.2 "authorization snapshot")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_or_service_identity: str = Field(min_length=1)
    case_roles: tuple[str, ...] = ()


class ExclusionRecord(BaseModel):
    """One explicitly excluded ref and the closed-set reason it was left out
    (master design section 10.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: UUID
    reason: ExclusionReason


class ContextManifest(BaseModel):
    """The persisted, hashable snapshot of exactly what one model call was
    authorized to see (master design section 10.2).

    Every ref is an opaque identifier into a versioned, authoritative record --
    never inline content.  ``compute_context_hash`` turns the manifest's
    content into a stable ``contextHash``; ``id`` and ``created_at`` identify
    the row, not the content, and are excluded from the hash.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ContextManifestId
    case_id: CaseId
    case_version: int = Field(ge=1)
    task_id: TaskId | None = None
    goal_contract_id: GoalContractId
    goal_contract_version: int = Field(ge=1)
    agent_role: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    model_version: str | None = None
    tool_versions: Mapping[str, str] = Field(default_factory=dict)
    authorization_snapshot: AuthorizationSnapshot
    authoritative_fact_refs: tuple[UUID, ...] = ()
    human_decision_refs: tuple[UUID, ...] = ()
    upstream_artifact_refs: tuple[UUID, ...] = ()
    open_gap_refs: tuple[UUID, ...] = ()
    open_conflict_refs: tuple[UUID, ...] = ()
    open_challenge_refs: tuple[UUID, ...] = ()
    retrieval_query_refs: tuple[UUID, ...] = ()
    tool_result_refs: tuple[UUID, ...] = ()
    explicit_exclusions: tuple[ExclusionRecord, ...] = ()
    budgets: BudgetSpec
    created_at: datetime


#: Manifest ref fields whose element order is not semantically meaningful and
#: is therefore sorted before hashing, so an identical set of refs hashes
#: identically regardless of the order it was supplied in.
_SORTED_REF_FIELDS: tuple[str, ...] = (
    "authoritative_fact_refs",
    "human_decision_refs",
    "upstream_artifact_refs",
    "open_gap_refs",
    "open_conflict_refs",
    "open_challenge_refs",
    "retrieval_query_refs",
    "tool_result_refs",
)


def compute_context_hash(manifest: ContextManifest) -> str:
    """Deterministic SHA-256 hex over a manifest's canonical content.

    ``id`` and ``created_at`` are excluded so the hash identifies content, not
    a row.  Every unordered ref list -- and the exclusion records -- are
    sorted, and ``json.dumps(sort_keys=True)`` canonicalizes every object key
    (including ``tool_versions`` and the authorization snapshot), so two
    manifests with the same content produce the same hash no matter what order
    their refs or tool versions were supplied in.
    """

    payload: dict[str, Any] = manifest.model_dump(mode="json", exclude={"id", "created_at"})
    for field_name in _SORTED_REF_FIELDS:
        payload[field_name] = sorted(payload[field_name])
    payload["explicit_exclusions"] = sorted(
        payload["explicit_exclusions"],
        key=lambda exclusion: (exclusion["ref"], exclusion["reason"]),
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
