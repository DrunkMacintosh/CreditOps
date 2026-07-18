"""Worker processor for CREDIT_OPERATIONS tasks.

Registered in the per-task-type ProcessorRegistry exactly like
INDEPENDENT_RISK_REVIEW.  Stages are checkpointed (evidence view built ->
deterministic package computed -> memo drafted -> package persisted) so a
redelivery resumes from the latest checkpoint; the persist itself is
idempotent on (case, case version, task), making duplicate delivery
harmless.  Mirrors ``application/risk_review/processor.py``: only the
INFERENCE-equivalent checkpoint (here, MEMO_DRAFTED) and the terminal
PERSISTED checkpoint short-circuit re-execution -- every earlier stage is a
pure, idempotent recomputation, so re-running it on retry is always safe.

Fail-closed paths:

- No configured FPT reasoning endpoint -> FAILED_MANUAL_REVIEW BEFORE any
  deterministic work even starts (mirrors every other agent's "no gateway"
  policy).
- Readiness already requires the INDEPENDENT_RISK_REVIEW handoff
  (READY_FOR_OPERATIONS) at the task-graph level
  (application/orchestration/graph.py); this processor re-checks at
  execution time that intake, underwriting, legal AND risk-review are ALL
  actually loadable and fails closed (FAILED_MANUAL_REVIEW) if any is
  missing -- defense in depth.  The deterministic package alone -- with no
  drafted memo narrative -- is NEVER persisted as if it were a completed
  package; a package row exists only once the memo has been validated.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from pydantic import ValidationError

from creditops.application.credit_ops.analysis import compute_deterministic_package
from creditops.application.credit_ops.assembler import (
    AssemblerRunContext,
    CreditOpsOutputInvalid,
    RunMemoInference,
    assemble_package,
    persist_credit_ops_package,
    target_universe_for,
)
from creditops.application.ports.credit_ops import CreditOpsRepository, PersistedCreditOpsOutput
from creditops.application.ports.model_gateway import InferenceError, InferenceUnavailableError
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.use_cases.run_worker_once import (
    CheckpointCallback,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.credit_ops import CREDIT_OPS_AGENT_ROLE, CreditOpsPackage

CHECKPOINT_EVIDENCE = "EVIDENCE_VIEW_BUILT"
CHECKPOINT_DETERMINISTIC_PACKAGE = "DETERMINISTIC_PACKAGE_COMPUTED"
CHECKPOINT_MEMO_DRAFTED = "MEMO_DRAFTED"
CHECKPOINT_PERSISTED = "PACKAGE_PERSISTED"


class CreditOperationsProcessor:
    """Run one resumable, idempotent credit-ops execution for a claimed task."""

    def __init__(
        self,
        repository: CreditOpsRepository,
        inference: RunMemoInference | None,
        *,
        clock: Callable[[], datetime] | None = None,
        execution_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._repository = repository
        self._inference = inference
        self._clock = clock or (lambda: datetime.now(UTC))
        self._execution_id_factory = execution_id_factory or uuid4

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_PERSISTED:
            return StageResult()

        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_MEMO_DRAFTED:
            return await self._persist_stage(
                task, self._package_from_checkpoint(checkpoint), save_checkpoint
            )

        existing = await self._repository.find_persisted(
            case_id=task.case_id, case_version=task.case_version, task_id=task.id
        )
        if existing is not None:
            await self._save_persisted_checkpoint(save_checkpoint, existing)
            return StageResult()

        if self._inference is None:
            await self._audit(
                task,
                "CREDIT_OPS_GATEWAY_UNAVAILABLE",
                {"reason": "no configured FPT reasoning endpoint"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "credit operations requires a configured FPT reasoning endpoint",
            )

        view = await self._repository.load_upstream_view(task.case_id)
        if view is None:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW, f"case {task.case_id} has no evidence view"
            )
        if view.case_version != task.case_version:
            return StageResult(
                WorkerOutcome.SUPERSEDED, "case version advanced past this task's bound version"
            )
        await save_checkpoint(
            CHECKPOINT_EVIDENCE,
            {
                "hasIntakeHandoff": view.has_intake_handoff,
                "hasUnderwriting": view.underwriting is not None,
                "hasLegal": view.legal is not None,
                "hasRiskReview": view.risk_review is not None,
                "builtAt": view.built_at.isoformat(),
            },
        )

        if not view.is_complete():
            # Defense-in-depth: readiness (graph.py) already requires the
            # risk-review READY_FOR_OPERATIONS handoff -- which itself
            # requires both maker handoffs -- before this task type may
            # become READY.  An incomplete view here means that invariant
            # was violated upstream; never draft a memo with nothing to
            # cite, and never persist a deterministic-only package as if it
            # were a completed one.
            await self._audit(
                task,
                "CREDIT_OPS_UPSTREAM_INCOMPLETE",
                {
                    "hasIntakeHandoff": view.has_intake_handoff,
                    "hasUnderwriting": view.underwriting is not None,
                    "hasLegal": view.legal is not None,
                    "hasRiskReview": view.risk_review is not None,
                },
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "credit operations requires intake, underwriting, legal AND independent "
                "risk review to all be present for this case version",
            )

        open_gaps = await self._repository.load_open_gaps(task.case_id, task.case_version)
        dispositions = await self._repository.load_dispositions(task.case_id, task.case_version)
        deterministic = compute_deterministic_package(
            view=view, open_gaps=open_gaps, dispositions=dispositions
        )
        await save_checkpoint(
            CHECKPOINT_DETERMINISTIC_PACKAGE,
            {
                "allRequiredPresent": deterministic.package_completeness.all_required_present,
                "documentRequestCount": len(deterministic.document_requests),
                "proposedActionCount": len(deterministic.proposed_actions),
            },
        )

        universe = target_universe_for(view)
        run = AssemblerRunContext(
            task_id=task.id,
            execution_id=self._execution_id_factory(),
            correlation_id=f"credit-ops:{task.id}",
        )
        try:
            memo, model_id, endpoint_id = await self._inference.infer(
                view=view, deterministic=deterministic, universe=universe, run=run
            )
        except InferenceUnavailableError:
            await self._audit(
                task,
                "CREDIT_OPS_GATEWAY_UNAVAILABLE",
                {"reason": "FPT reasoning endpoint unavailable"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW, "FPT reasoning endpoint unavailable"
            )
        except (CreditOpsOutputInvalid, InferenceError, ValidationError) as exc:
            await self._audit(task, "CREDIT_OPS_OUTPUT_REJECTED", {"reason": str(exc)[:2000]})
            return StageResult(WorkerOutcome.RETRY_WAIT, f"credit-ops output rejected: {exc}")

        package = assemble_package(
            view=view,
            deterministic=deterministic,
            memo=memo,
            run=run,
            model_id=model_id,
            endpoint_id=endpoint_id,
            clock=self._clock,
        )
        await save_checkpoint(
            CHECKPOINT_MEMO_DRAFTED, {"package": package.model_dump(mode="json")}
        )
        return await self._persist_stage(task, package, save_checkpoint)

    async def _persist_stage(
        self,
        task: TaskRecord,
        package: CreditOpsPackage,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        persisted = await persist_credit_ops_package(
            self._repository, package, handoff_id=self._handoff_id_for(package)
        )
        await self._save_persisted_checkpoint(save_checkpoint, persisted)
        await self._audit(
            task,
            "CREDIT_OPS_PACKAGE_PERSISTED",
            {
                "packageId": str(persisted.package_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "executionId": str(package.provenance.execution_id),
                "proposedActionCount": len(package.proposed_actions),
                "documentRequestCount": len(package.document_requests),
            },
        )
        return StageResult()

    @staticmethod
    def _handoff_id_for(package: CreditOpsPackage) -> UUID:
        return uuid5(NAMESPACE_OID, f"credit-ops-handoff:{package.id}")

    @staticmethod
    def _package_from_checkpoint(checkpoint: TaskCheckpoint) -> CreditOpsPackage:
        raw = checkpoint.checkpoint_data.get("package")
        if not isinstance(raw, Mapping):
            raise CreditOpsOutputInvalid("memo-drafted checkpoint has no package payload")
        return CreditOpsPackage.model_validate(raw)

    async def _save_persisted_checkpoint(
        self, save_checkpoint: CheckpointCallback, persisted: PersistedCreditOpsOutput
    ) -> None:
        await save_checkpoint(
            CHECKPOINT_PERSISTED,
            {
                "packageId": str(persisted.package_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "created": persisted.created,
            },
        )

    async def _audit(
        self, task: TaskRecord, event_type: str, event_data: Mapping[str, Any]
    ) -> None:
        await self._repository.append_audit(
            OrchestrationAuditEvent(
                case_id=task.case_id,
                case_version=task.case_version,
                event_type=event_type,
                execution_id=self._execution_id_factory(),
                artifact_type="PROCESSING_TASK",
                artifact_id=task.id,
                event_data={
                    "role": CREDIT_OPS_AGENT_ROLE,
                    "recordedAt": self._clock().isoformat(),
                    **dict(event_data),
                },
            )
        )
