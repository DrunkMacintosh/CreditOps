"""Read-only contract for the ``/cong-viec`` (my work queue) surface.

The work queue is the spec's default entry surface (master design section
17.1): work items are DERIVED from server capabilities -- the actor's active
case assignments AND their human-gate / task state -- never asserted by the
client.  Reading the queue grants no authority: an item only tells its holder
*where* an authorized human action is waiting, it can neither perform that
action nor widen who may.

This port exposes exactly ONE read method and NO write of any kind.  It can
neither satisfy a gate, resolve a gap, confirm a fact, nor record a decision;
it only assembles a bounded, deterministic list of pending items for one actor.
The ``roles`` handed in are the actor's JWT roles already intersected with the
case-participant vocabulary at the API layer (see ``api/work_items.py``); the
adapter ANDs them against the actor's server-side assignment roles in SQL, so a
role held on a case but absent from the token yields nothing (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

# PROPOSED synthetic triage levels (design section 2 label PROPOSED); NOT an
# official SHB prioritisation.  BLOCKING = a human must act before the case can
# progress; ATTENTION = an authorized human action is pending; INFO = the
# system will proceed on its own (surfaced only for visibility).
Severity = Literal["BLOCKING", "ATTENTION", "INFO"]


@dataclass(frozen=True, slots=True)
class WorkItem:
    """One pending, deterministically-derived item in an actor's work queue.

    Immutable and provenance-free by construction: it carries only the case
    coordinates, a synthetic ``kind``, Vietnamese display copy, a PROPOSED
    ``severity`` and the concrete frontend ``primary_route`` the holder should
    open.  It never carries a document body, secret, or authority grant.
    """

    case_id: UUID
    case_version: int
    kind: str
    title_vi: str
    reason_vi: str
    severity: Severity
    primary_route: str  # concrete frontend path, e.g. '/ho-so/<caseId>/khoang-trong'
    created_at: datetime


class WorkItemRepository(Protocol):
    async def list_for_actor(
        self,
        actor_id: UUID,
        roles: frozenset[str],
        *,
        limit: int,
    ) -> tuple[WorkItem, ...]: ...
