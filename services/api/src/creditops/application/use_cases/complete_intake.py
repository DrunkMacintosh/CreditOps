"""Assigned-intake completion -> immutable IntakeHandoff -> G1 (master design
section 5 stage 3, section 9, section 23).

The intake officer's "complete intake" action succeeds only when the confirmed
evidence state is genuinely complete.  Completeness is NOT re-implemented here:
the use case loads the confirmed evidence into the frozen ``HandoffArtifact``
and lets ``domain/handoffs.py``'s pydantic validator be the gate -- every
Candidate Fact needs exactly one disposition, every supported disposition
exactly one matching Confirmed Fact, all bound to one case version.  A
validation failure surfaces the domain reasons unchanged as an
``IntakeIncompleteError``; nothing is persisted and no orchestration runs
(fail closed).

On success the handoff row is written idempotently (a repeat completion returns
the existing handoff with ``created=False`` and does nothing else), a human
audit event is appended, and -- when an orchestration repository is wired -- an
idempotent orchestration tick is kicked off with ``trigger_ref="HANDOFF:{id}"``
so the deterministic engine takes over (the actual queue publish is the API's
best-effort ``DispatchOutbox`` step, mirroring ``api/risk_review.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import ValidationError

from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.ports.intake import IntakeAuditEvent, IntakeRepository
from creditops.application.ports.orchestration import OrchestrationRepository
from creditops.domain.handoffs import HandoffArtifact
from creditops.observability import log_event

INTAKE_HANDOFF_CREATED_EVENT = "INTAKE_HANDOFF_CREATED"
_VALUE_ERROR_PREFIX = "Value error, "

_logger = logging.getLogger(__name__)


class IntakeIncompleteError(Exception):
    """The confirmed intake evidence is not complete enough to hand off.

    Carries the domain validator's own reasons (the authoritative completeness
    verdict) so the API can list the unresolved items in its error ``details``.
    """

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("intake evidence is not complete for a handoff")
        self.reasons = reasons


@dataclass(frozen=True, slots=True)
class IntakeCompletionResult:
    handoff_id: UUID
    case_version: int
    state: str
    created: bool


def _incomplete_reasons(exc: ValidationError) -> tuple[str, ...]:
    reasons: list[str] = []
    for error in exc.errors():
        message = str(error.get("msg", "")).strip()
        if message.startswith(_VALUE_ERROR_PREFIX):
            message = message[len(_VALUE_ERROR_PREFIX) :]
        if message and message not in reasons:
            reasons.append(message)
    if not reasons:
        reasons.append("bằng chứng tiếp nhận chưa đủ điều kiện bàn giao")
    return tuple(reasons)


class CompleteIntake:
    def __init__(
        self,
        repository: IntakeRepository,
        orchestration: OrchestrationRepository | None = None,
        *,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._orchestration = orchestration
        self._id_factory = id_factory or uuid4

    async def execute(
        self, case_id: UUID, case_version: int, actor_id: UUID
    ) -> IntakeCompletionResult:
        existing = await self._repository.load_current_handoff(case_id, case_version)
        if existing is not None:
            return IntakeCompletionResult(
                handoff_id=existing.id,
                case_version=existing.case_version,
                state=existing.state,
                created=False,
            )

        view = await self._repository.load_intake_evidence(case_id, case_version)
        try:
            handoff = HandoffArtifact(
                id=self._id_factory(),
                case_id=case_id,
                case_version=case_version,
                candidates=view.candidates,
                confirmations=view.confirmations,
                confirmed_facts=view.confirmed_facts,
                conflict_ids=view.conflict_ids,
                gap_ids=view.gap_ids,
            )
        except ValidationError as exc:
            raise IntakeIncompleteError(_incomplete_reasons(exc)) from exc

        persisted = await self._repository.persist_handoff(handoff, actor_id=actor_id)
        if persisted.created:
            await self._repository.append_audit(
                IntakeAuditEvent(
                    case_id=case_id,
                    case_version=case_version,
                    event_type=INTAKE_HANDOFF_CREATED_EVENT,
                    actor_id=actor_id,
                    artifact_type="HANDOFF",
                    artifact_id=persisted.handoff_id,
                    event_data={
                        "confirmedFactCount": len(view.confirmed_facts),
                        "conflictCount": len(view.conflict_ids),
                        "gapCount": len(view.gap_ids),
                    },
                )
            )
            await self._kickoff(case_id, persisted.handoff_id)

        return IntakeCompletionResult(
            handoff_id=persisted.handoff_id,
            case_version=case_version,
            state=handoff.state,
            created=persisted.created,
        )

    async def _kickoff(self, case_id: UUID, handoff_id: UUID) -> None:
        """Self-fire an idempotent orchestration tick after the handoff is
        durable (master design section 9).  The tick creates the plan task and
        its TASK_READY outbox event atomically; the queue publish is the API's
        best-effort ``DispatchOutbox`` follow-up.  A tick failure must never
        fail the human's already-durable handoff, but it is logged, never
        silent (fail-open on the tick, fail-closed on the handoff)."""

        if self._orchestration is None:
            return
        try:
            result = await KickoffOrchestration(self._orchestration).execute(
                case_id, trigger_ref=f"HANDOFF:{handoff_id}"
            )
            log_event(
                _logger,
                logging.INFO,
                "Orchestration kickoff after intake handoff",
                {
                    "event": "intake_handoff_kickoff",
                    "handoffId": str(handoff_id),
                    "created": result.created,
                },
            )
        except Exception:
            log_event(
                _logger,
                logging.ERROR,
                "Orchestration kickoff after intake handoff failed; the handoff "
                "is durable and the case can be advanced manually",
                {"event": "intake_handoff_kickoff_failed", "handoffId": str(handoff_id)},
            )
