"""Durable Postgres adapter for agent governance (master design sections 10.1,
10.2, 13; P0 #12).

Append-only writes only: a context-manifest insert deduplicated per
``(task_id, context_hash)`` (the partial unique index of migration
202607180027) and a one-time idempotent seed of the committed goal-contract
registry into ``public.goal_contracts`` on its ``(contract_key, version)``
append-only key.  Nothing here reads, updates, or deletes governance state --
the workforce never reads governance internals and history is never rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from creditops.application.ports.governance import PersistedManifest
from creditops.domain.goal_contracts import (
    ContextManifest,
    GoalContract,
    compute_context_hash,
)
from creditops.infrastructure.postgres.orchestration import ConnectionFactory


class PostgresGovernanceRepository:
    """Append-only durable surface for goal contracts and context manifests."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def persist_manifest(self, manifest: ContextManifest) -> PersistedManifest:
        context_hash = compute_context_hash(manifest)
        # The jsonb snapshot carries the full ordered content (refs only); the
        # surrogate id and wall-clock created_at live in their own columns and
        # are excluded from the hash, so they are excluded here too.
        snapshot = manifest.model_dump(mode="json", exclude={"id", "created_at"})
        async with self._connection_factory() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    insert into public.agent_context_manifests (
                      id, case_id, case_version, task_id, goal_contract_id,
                      goal_contract_version, agent_role, profile_version,
                      prompt_version, schema_version, model_version,
                      context_hash, manifest
                    ) values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    on conflict (task_id, context_hash) where task_id is not null
                    do nothing
                    returning id
                    """,
                    (
                        manifest.id,
                        manifest.case_id,
                        manifest.case_version,
                        manifest.task_id,
                        manifest.goal_contract_id,
                        manifest.goal_contract_version,
                        manifest.agent_role,
                        manifest.profile_version,
                        manifest.prompt_version,
                        manifest.schema_version,
                        manifest.model_version,
                        context_hash,
                        Jsonb(snapshot),
                    ),
                )
                inserted = await cursor.fetchone()
                if inserted is not None:
                    return PersistedManifest(
                        manifest_id=cast(UUID, inserted[0]),
                        context_hash=context_hash,
                        created=True,
                    )
                # A prior delivery already persisted this exact content for the
                # task; the store is append-only, so return the existing id so
                # every caller references one durable row.
                cursor = await connection.execute(
                    """
                    select id from public.agent_context_manifests
                    where task_id = %s and context_hash = %s
                    """,
                    (manifest.task_id, context_hash),
                )
                existing = await cursor.fetchone()
                if existing is None:
                    raise RuntimeError("context manifest idempotency row disappeared")
                return PersistedManifest(
                    manifest_id=cast(UUID, existing[0]),
                    context_hash=context_hash,
                    created=False,
                )

    async def ensure_goal_contract_rows(
        self, contracts: Sequence[GoalContract]
    ) -> None:
        async with self._connection_factory() as connection:
            async with connection.transaction():
                for contract in contracts:
                    await connection.execute(
                        """
                        insert into public.goal_contracts (
                          id, contract_key, version, objective_vi,
                          allowed_actions, prohibited_actions,
                          success_conditions_vi, required_evidence_kinds,
                          output_schema_ref, output_schema_version,
                          required_human_gate, max_input_tokens,
                          max_output_tokens, max_tool_calls
                        ) values (
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        on conflict (contract_key, version) do nothing
                        """,
                        (
                            contract.id,
                            contract.contract_key,
                            contract.version,
                            contract.objective_vi,
                            Jsonb(list(contract.allowed_actions)),
                            Jsonb(list(contract.prohibited_actions)),
                            Jsonb(list(contract.success_conditions_vi)),
                            Jsonb(list(contract.required_evidence_kinds)),
                            contract.output_schema_ref,
                            contract.output_schema_version,
                            contract.required_human_gate,
                            contract.budgets.max_input_tokens,
                            contract.budgets.max_output_tokens,
                            contract.budgets.max_tool_calls,
                        ),
                    )
