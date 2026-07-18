"""The FORWARD path of a ``MAKER_MUST_REVISE`` risk disposition (master design
section 9; section 5 stage 6).

Recording ``MAKER_MUST_REVISE`` leaves ``G3_RISK_DISPOSITION`` OPEN (a revise
directive is not a continue authorization) -- correct, but on its own it
schedules nothing and the case stalls.  This use case is the missing feedback
edge: triggered ONLY from a human ``MAKER_MUST_REVISE`` challenge disposition,
it bumps the case version and kicks off a fresh orchestration tick so the maker
analysis reruns on the new version while the evidence base carries forward
unchanged.

Two durable effects, in order:

1. ``bump_case_version`` (optimistic, single transaction in the orchestration
   repository): increment the version guarded by ``expected_version``, append a
   ``CASE_VERSION_BUMPED`` audit row, and re-issue the intake handoff at the new
   version.  A lost optimistic race raises ``StaleCaseVersionError``, which
   propagates to the guarded caller -- nothing is kicked off on a version the
   caller never observed (fail closed).
2. ``KickoffOrchestration`` with ``trigger_ref="REVISE:{disposition_id}"``:
   create one ``ORCHESTRATOR_PLAN`` task at the new version (plus its TASK_READY
   outbox event, atomically).  ``AdvanceCase`` then schedules fresh
   ``CREDIT_UNDERWRITING`` + ``LEGAL_COMPLIANCE_COLLATERAL`` (G1 satisfied by the
   re-issued handoff), fences the old-version tasks as superseded, and keeps
   Independent Risk Review blocked on the new-version G2 -- only the invalidated
   nodes rerun, the whole case never restarts.

The queue publish (``DispatchOutbox``) is intentionally NOT done here: like the
G3 retick in ``api/risk_review.py``, the durable plan task + outbox event commit
here and the best-effort queue send happens at the API boundary where the queue
is wired (the recovery sweep covers anything left).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.ports.orchestration import OrchestrationRepository


@dataclass(frozen=True, slots=True)
class RequestMakerRevisionResult:
    case_id: UUID
    previous_version: int
    new_version: int
    #: The ``ORCHESTRATOR_PLAN`` task the REVISE tick created (or the existing
    #: one, if a duplicate trigger already created it -- ``plan_created`` says
    #: which).
    plan_task_id: UUID
    plan_created: bool


class RequestMakerRevision:
    def __init__(
        self,
        repository: OrchestrationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
        execution_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4
        self._execution_id_factory = execution_id_factory or uuid4

    async def execute(
        self,
        *,
        case_id: UUID,
        expected_version: int,
        disposition_id: UUID,
        actor_id: UUID,
        reason: str,
    ) -> RequestMakerRevisionResult:
        new_version = await self._repository.bump_case_version(
            case_id,
            expected_version=expected_version,
            reason=reason,
            disposition_ref=f"risk-review-disposition:{disposition_id}",
            actor_id=actor_id,
        )
        # StaleCaseVersionError from the bump propagates here (before any tick):
        # a stale disposition never schedules work on the new version.
        kickoff = await KickoffOrchestration(
            self._repository,
            clock=self._clock,
            id_factory=self._id_factory,
            execution_id_factory=self._execution_id_factory,
        ).execute(case_id, trigger_ref=f"REVISE:{disposition_id}")
        return RequestMakerRevisionResult(
            case_id=case_id,
            previous_version=expected_version,
            new_version=new_version,
            plan_task_id=kickoff.task_id,
            plan_created=kickoff.created,
        )
