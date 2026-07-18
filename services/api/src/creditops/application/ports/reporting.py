"""Read-only contract for the operations-reporting surface (``/bao-cao-van-hanh``).

Master design section 17.1: an aggregate operations dashboard for the
``REPORTING_VIEWER`` role.  This port exposes exactly ONE read method and NO
write of any kind.  What it returns is deliberately **provenance-free**: only
grouped COUNTS keyed by a status / type / stage / age bucket -- never a case
identifier, document body, secret, or any per-case row.  Reading the report
grants no authority and reveals no case content; it cannot be used to reach a
single case.

The severity / bucket vocabularies are the same CLOSED, PROPOSED synthetic sets
already committed elsewhere in the codebase (task/gate/stage/alert enums); this
port merely tallies them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StatusCount:
    """A grouped count keyed by a single status label (no case identity)."""

    status: str
    count: int


@dataclass(frozen=True, slots=True)
class QueueAgeBucketCount:
    """Count of queued tasks in one (status, age-bucket) cell.

    ``bucket`` is a PROPOSED synthetic age band; the age is computed in SQL as
    ``clock_timestamp() - available_at`` so no wall-clock value crosses the
    boundary and no per-task timestamp is exposed.
    """

    status: str
    bucket: str
    count: int


@dataclass(frozen=True, slots=True)
class GateStatusCount:
    """Count of human gates in one (gate_type, status) cell."""

    gate_type: str
    status: str
    count: int


@dataclass(frozen=True, slots=True)
class StageCount:
    """Count of document versions in one ingestion ``stage``."""

    stage: str
    count: int


@dataclass(frozen=True, slots=True)
class OutboxBacklog:
    """The transactional-outbox backlog: how many events are still
    undispatched and the largest dispatch-attempt count among them.

    Both are scalars -- no event id, payload, or case identity.
    """

    undispatched_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class OperationsMetrics:
    """One immutable, aggregate snapshot of operational health.

    Every field is a grouped count or scalar; by construction there is no case
    id, document content, or secret anywhere in this structure.
    """

    tasks_by_status: tuple[StatusCount, ...]
    queue_age_buckets: tuple[QueueAgeBucketCount, ...]
    human_gates: tuple[GateStatusCount, ...]
    outbox: OutboxBacklog
    documents_by_stage: tuple[StageCount, ...]
    alerts_by_status: tuple[StatusCount, ...]


class ReportingRepository(Protocol):
    async def load_operations_metrics(self) -> OperationsMetrics: ...
