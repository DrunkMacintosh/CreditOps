"""Durable-state contract for agent governance (master design sections 10.1,
10.2, 13).

The governance repository is the ONLY seam through which a processor turns the
committed :class:`~creditops.domain.goal_contracts.GoalContract` registry and a
freshly-built :class:`~creditops.domain.goal_contracts.ContextManifest` into
durable rows.  It exposes exactly two writes and nothing else:

- ``persist_manifest`` -- append the ordered, hashable snapshot of everything
  one model call was authorized to see, idempotent per ``(task, context_hash)``
  so a redelivery that re-runs the pre-inference stage never writes a second
  row for the same content.
- ``ensure_goal_contract_rows`` -- seed the committed registry contracts into
  ``public.goal_contracts`` once at composition time, idempotent on the
  ``(contract_key, version)`` append-only key.

There is deliberately no read/update/delete surface: the governance stores are
append-only and the workforce never reads governance internals.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creditops.domain.goal_contracts import ContextManifest, GoalContract


@dataclass(frozen=True, slots=True)
class PersistedManifest:
    """Durable identity of one persisted context manifest.

    ``created`` is ``False`` when the manifest already existed for this
    ``(task, context_hash)`` -- the idempotent redelivery path -- so a caller
    can tell a first write from a deduplicated one.  ``manifest_id`` is always
    the durable row's id (the existing row's id on a conflict), never a fresh
    surrogate that would diverge from the stored snapshot.
    """

    manifest_id: UUID
    context_hash: str
    created: bool


class GovernanceRepository(Protocol):
    """Append-only durable surface for goal contracts and context manifests."""

    async def persist_manifest(self, manifest: ContextManifest) -> PersistedManifest:
        """Append ``manifest``; idempotent per ``(task_id, context_hash)``."""
        ...

    async def ensure_goal_contract_rows(
        self, contracts: Sequence[GoalContract]
    ) -> None:
        """Seed ``contracts`` idempotently on their ``(contract_key, version)``."""
        ...
