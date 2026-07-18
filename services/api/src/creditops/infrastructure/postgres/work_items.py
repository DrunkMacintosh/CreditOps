"""Durable Postgres adapter for the ``/cong-viec`` work queue.

ONE deterministic, strictly read-only assembly.  Every statement here is a
``select``: there is NO ``insert`` / ``update`` / ``delete`` anywhere in this
module (``tests/contract/postgres/test_work_items_adapter.py`` proves this at
the source-text and captured-SQL level).  Reading the queue never mutates
state and never grants authority.

Each rule below is exactly ONE select over the actor's own active assignments,
role-filtered in SQL by ``officer_id = <actor>`` AND ``revoked_at is null`` AND
a ``case_role`` predicate.  The JWT/assignment AND is completed at the API
layer: a rule runs only when the corresponding role is present in the ``roles``
handed in (already intersected with the actor's JWT claims), so a case role
held without the matching token role surfaces nothing.  No case is probed
outside the actor's assignments, so the queue leaks no capability.

All Vietnamese display copy lives in ONE module-level mapping; the severity
mapping is PROPOSED synthetic and commented as such.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from creditops.application.orchestration.roles import (
    INTAKE_OFFICER_ROLE,
    OPS_OFFICER_ROLE,
    RISK_REVIEWER_ROLE,
)
from creditops.application.ports.work_items import Severity, WorkItem
from creditops.infrastructure.postgres.orchestration import ConnectionFactory
from creditops.infrastructure.postgres.repositories import DatabaseConnection

# -- Display copy: ONE module-level mapping, no scattered Vietnamese literals. --
# kind -> (title_vi, reason_vi)
_ITEM_COPY: dict[str, tuple[str, str]] = {
    "INTAKE_INCOMPLETE": (
        "Hoàn tất tiếp nhận hồ sơ",
        "Hồ sơ chưa có bàn giao tiếp nhận ở phiên bản hiện tại; cần hoàn tất bước tiếp nhận.",
    ),
    "GAP_BATCH_PENDING": (
        "Duyệt lô yêu cầu bổ sung chứng từ",
        "Cổng G2 đang mở: lô yêu cầu bổ sung chứng từ đang chờ người có thẩm quyền xử lý.",
    ),
    "RISK_DISPOSITION_PENDING": (
        "Xử lý ý kiến rà soát rủi ro",
        "Cổng G3 đang mở và đã có báo cáo rà soát rủi ro; cần con người xử lý các thách thức.",
    ),
    "OPS_AUTHORIZATION_PENDING": (
        "Phê duyệt hồ sơ tác nghiệp tín dụng",
        "Cổng G4 đang mở và đã có gói tác nghiệp tín dụng; cần con người phê duyệt.",
    ),
    "MANUAL_REVIEW": (
        "Rà soát thủ công tác vụ bị lỗi",
        "Có tác vụ FAILED_MANUAL_REVIEW ở phiên bản hiện tại; cần con người rà soát.",
    ),
    "RETRY_WAIT": (
        "Tác vụ đang chờ thử lại",
        "Có tác vụ RETRY_WAIT ở phiên bản hiện tại; hệ thống sẽ tự thử lại.",
    ),
}

# -- PROPOSED synthetic severity mapping (design section 2 label PROPOSED). ----
# These are prototype triage levels, NOT an official SHB prioritisation.  Only
# MANUAL_REVIEW (BLOCKING) and RETRY_WAIT (INFO) are pinned by the task brief;
# the pending-human-gate items are PROPOSED as ATTENTION.
_ITEM_SEVERITY: dict[str, Severity] = {
    "INTAKE_INCOMPLETE": "ATTENTION",
    "GAP_BATCH_PENDING": "ATTENTION",
    "RISK_DISPOSITION_PENDING": "ATTENTION",
    "OPS_AUTHORIZATION_PENDING": "ATTENTION",
    "MANUAL_REVIEW": "BLOCKING",
    "RETRY_WAIT": "INFO",
}

# -- Frontend route templates (design section 17.1 route map). ----------------
# kind -> path template; the concrete case id is substituted per item.
_ITEM_ROUTE: dict[str, str] = {
    "INTAKE_INCOMPLETE": "/ho-so/{case_id}/tiep-nhan",
    "GAP_BATCH_PENDING": "/ho-so/{case_id}/khoang-trong",
    "RISK_DISPOSITION_PENDING": "/ho-so/{case_id}/rui-ro",
    "OPS_AUTHORIZATION_PENDING": "/ho-so/{case_id}/tong-hop",
    "MANUAL_REVIEW": "/ho-so/{case_id}/quy-trinh",
    "RETRY_WAIT": "/ho-so/{case_id}/quy-trinh",
}

# Rule 1: INTAKE_OFFICER assignment with no live intake handoff at the current
# case version.  created_at falls back to the case's own creation instant.
_SQL_INTAKE_INCOMPLETE = """
    select cc.id, cc.case_version, cc.created_at
    from public.credit_cases as cc
    join public.case_assignments as a
      on a.case_id = cc.id
     and a.officer_id = %s
     and a.revoked_at is null
     and a.case_role = 'INTAKE_OFFICER'
    where not exists (
      select 1 from public.handoffs as h
      where h.case_id = cc.id
        and h.case_version = cc.case_version
        and h.state = 'READY_FOR_SPECIALIST_REVIEW'
        and h.stale_at is null
    )
    order by cc.created_at desc
    limit %s
"""

# Rule 2: INTAKE_OFFICER assignment with an OPEN G2 gate at the current version.
_SQL_GAP_BATCH_PENDING = """
    select cc.id, cc.case_version, g.created_at
    from public.credit_cases as cc
    join public.case_assignments as a
      on a.case_id = cc.id
     and a.officer_id = %s
     and a.revoked_at is null
     and a.case_role = 'INTAKE_OFFICER'
    join public.human_gates as g
      on g.case_id = cc.id
     and g.case_version = cc.case_version
     and g.gate_type = 'G2_GAP_REQUEST_APPROVAL'
     and g.status = 'OPEN'
    order by g.created_at desc
    limit %s
"""

# Rule 3: RISK_REVIEWER assignment, OPEN G3 gate, and an existing risk
# assessment at the current version.
_SQL_RISK_DISPOSITION_PENDING = """
    select cc.id, cc.case_version, g.created_at
    from public.credit_cases as cc
    join public.case_assignments as a
      on a.case_id = cc.id
     and a.officer_id = %s
     and a.revoked_at is null
     and a.case_role = 'RISK_REVIEWER'
    join public.human_gates as g
      on g.case_id = cc.id
     and g.case_version = cc.case_version
     and g.gate_type = 'G3_RISK_DISPOSITION'
     and g.status = 'OPEN'
    where exists (
      select 1 from public.risk_review_assessments as r
      where r.case_id = cc.id
        and r.case_version = cc.case_version
    )
    order by g.created_at desc
    limit %s
"""

# Rule 4: OPS_OFFICER assignment, OPEN G4 gate, and an existing credit-ops
# package at the current version.
_SQL_OPS_AUTHORIZATION_PENDING = """
    select cc.id, cc.case_version, g.created_at
    from public.credit_cases as cc
    join public.case_assignments as a
      on a.case_id = cc.id
     and a.officer_id = %s
     and a.revoked_at is null
     and a.case_role = 'OPS_OFFICER'
    join public.human_gates as g
      on g.case_id = cc.id
     and g.case_version = cc.case_version
     and g.gate_type = 'G4_OPS_AUTHORIZATION'
     and g.status = 'OPEN'
    where exists (
      select 1 from public.credit_ops_packages as p
      where p.case_id = cc.id
        and p.case_version = cc.case_version
    )
    order by g.created_at desc
    limit %s
"""

# Rules 5 & 6: any participant role on the case (case_role limited to the
# actor's authorized roles) with a task in the given status at the current
# version.  Collapsed to one row per case via ``max(created_at)`` so several
# failed/waiting tasks do not produce duplicate items.  The ``case_role``
# predicate is an ANY over the authorized-role list handed in.
_SQL_TASK_STATUS = """
    select cc.id, cc.case_version, max(pt.created_at) as created_at
    from public.credit_cases as cc
    join public.case_assignments as a
      on a.case_id = cc.id
     and a.officer_id = %s
     and a.revoked_at is null
     and a.case_role = any(%s)
    join public.processing_tasks as pt
      on pt.case_id = cc.id
     and pt.case_version = cc.case_version
     and pt.status = %s
    group by cc.id, cc.case_version
    order by created_at desc
    limit %s
"""

# Severity ordering: BLOCKING first, then everything else by created_at desc
# (design brief).  A single stable key expresses "BLOCKING group first, newest
# first within each group".
_BLOCKING_FIRST = 0
_NOT_BLOCKING = 1


class PostgresWorkItemRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def list_for_actor(
        self,
        actor_id: UUID,
        roles: frozenset[str],
        *,
        limit: int,
    ) -> tuple[WorkItem, ...]:
        if not roles or limit <= 0:
            return ()

        # ``case_role = any(...)`` list for the participant-wide task rules:
        # sorted for deterministic parameter capture.
        authorized_roles = sorted(roles)

        items: list[WorkItem] = []
        async with self._connection_factory() as connection:
            if INTAKE_OFFICER_ROLE in roles:
                items.extend(
                    self._items(
                        "INTAKE_INCOMPLETE",
                        await _rows(connection, _SQL_INTAKE_INCOMPLETE, (actor_id, limit)),
                    )
                )
                items.extend(
                    self._items(
                        "GAP_BATCH_PENDING",
                        await _rows(connection, _SQL_GAP_BATCH_PENDING, (actor_id, limit)),
                    )
                )
            if RISK_REVIEWER_ROLE in roles:
                items.extend(
                    self._items(
                        "RISK_DISPOSITION_PENDING",
                        await _rows(
                            connection, _SQL_RISK_DISPOSITION_PENDING, (actor_id, limit)
                        ),
                    )
                )
            if OPS_OFFICER_ROLE in roles:
                items.extend(
                    self._items(
                        "OPS_AUTHORIZATION_PENDING",
                        await _rows(
                            connection, _SQL_OPS_AUTHORIZATION_PENDING, (actor_id, limit)
                        ),
                    )
                )
            # Any participant role: failed / waiting tasks at the current version.
            items.extend(
                self._items(
                    "MANUAL_REVIEW",
                    await _rows(
                        connection,
                        _SQL_TASK_STATUS,
                        (actor_id, authorized_roles, "FAILED_MANUAL_REVIEW", limit),
                    ),
                )
            )
            items.extend(
                self._items(
                    "RETRY_WAIT",
                    await _rows(
                        connection,
                        _SQL_TASK_STATUS,
                        (actor_id, authorized_roles, "RETRY_WAIT", limit),
                    ),
                )
            )

        items.sort(key=_sort_key)
        return tuple(items[:limit])

    def _items(self, kind: str, rows: Sequence[Sequence[Any]]) -> list[WorkItem]:
        return [self._item(kind, row) for row in rows]

    def _item(self, kind: str, row: Sequence[Any]) -> WorkItem:
        case_id = cast(UUID, row[0])
        title_vi, reason_vi = _ITEM_COPY[kind]
        return WorkItem(
            case_id=case_id,
            case_version=int(row[1]),
            kind=kind,
            title_vi=title_vi,
            reason_vi=reason_vi,
            severity=_ITEM_SEVERITY[kind],
            primary_route=_ITEM_ROUTE[kind].format(case_id=case_id),
            created_at=cast(datetime, row[2]),
        )


def _sort_key(item: WorkItem) -> tuple[int, float]:
    rank = _BLOCKING_FIRST if item.severity == "BLOCKING" else _NOT_BLOCKING
    return (rank, -item.created_at.timestamp())


async def _rows(
    connection: DatabaseConnection, query: str, params: tuple[object, ...]
) -> Sequence[Sequence[Any]]:
    cursor = await connection.execute(query, params)
    return await cursor.fetchall()
