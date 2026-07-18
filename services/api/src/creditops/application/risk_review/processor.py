"""Worker processor for INDEPENDENT_RISK_REVIEW tasks.

Registered in the per-task-type ProcessorRegistry exactly like
CREDIT_UNDERWRITING and LEGAL_COMPLIANCE_COLLATERAL.  Stages are checkpointed
(evidence view -> maker outputs loaded -> pre-analysis computed -> inference
validated -> persisted) so a redelivery resumes from the latest checkpoint;
the persist itself is idempotent on (case, case version, task), making
duplicate delivery harmless.

Two fail-closed paths beyond the shared "no gateway" policy:

- Readiness already requires both maker handoffs at the task-graph level
  (application/orchestration/graph.py); this processor re-checks at execution
  time that BOTH maker outputs are actually loadable and fails closed
  (FAILED_MANUAL_REVIEW) if either is missing -- defense in depth against a
  graph/readiness bug ever letting a partial pair through.
- The same-execution guard: if this checker execution's id would equal
  either reviewed maker's execution id, the task fails closed BEFORE any
  model call -- "the same role execution must not author and independently
  clear the same material conclusion."
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from pydantic import ValidationError

from creditops.application.ports.model_gateway import (
    InferenceError,
    InferenceUnavailableError,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.ports.risk_review import (
    PersistedCheckerOutput,
    RiskReviewRepository,
)
from creditops.application.risk_review.analysis import compute_deterministic_pre_analysis
from creditops.application.risk_review.checker import (
    CheckerOutputInvalid,
    CheckerRunContext,
    RunCheckerInference,
    SameExecutionGuardTriggered,
    persist_checker_output,
)
from creditops.application.risk_review.evidence import build_target_universe
from creditops.application.use_cases.run_worker_once import (
    CheckpointCallback,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.risk_review import RISK_REVIEW_AGENT_ROLE, RiskReviewAssessment

CHECKPOINT_EVIDENCE_VIEW = "EVIDENCE_VIEW_BUILT"
CHECKPOINT_MAKER_OUTPUTS = "MAKER_OUTPUTS_LOADED"
CHECKPOINT_PRE_ANALYSIS = "PRE_ANALYSIS_COMPUTED"
CHECKPOINT_INFERENCE = "INFERENCE_VALIDATED"
CHECKPOINT_PERSISTED = "ASSESSMENT_PERSISTED"


class IndependentRiskReviewProcessor:
    """Run one resumable, idempotent checker execution for a claimed task."""

    def __init__(
        self,
        repository: RiskReviewRepository,
        inference: RunCheckerInference | None,
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

        if checkpoint is not None and checkpoint.checkpoint_type == CHECKPOINT_INFERENCE:
            return await self._persist_stage(
                task, self._assessment_from_checkpoint(checkpoint), save_checkpoint
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
                "RISK_REVIEW_GATEWAY_UNAVAILABLE",
                {"reason": "no configured FPT reasoning endpoint"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "independent risk review requires a configured FPT reasoning endpoint",
            )

        view = await self._repository.load_evidence_view(task.case_id)
        if view is None:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW, f"case {task.case_id} has no evidence view"
            )
        if view.case_version != task.case_version:
            return StageResult(
                WorkerOutcome.SUPERSEDED, "case version advanced past this task's bound version"
            )
        await save_checkpoint(
            CHECKPOINT_EVIDENCE_VIEW,
            {
                "confirmedFactCount": len(view.confirmed_facts),
                "builtAt": view.built_at.isoformat(),
            },
        )

        outputs = await self._repository.load_maker_outputs(task.case_id, task.case_version)
        if not outputs.is_complete():
            # Defense-in-depth: readiness (graph.py) already requires both
            # maker handoffs before this task type may become READY.  A
            # partial pair here means that invariant was violated upstream;
            # never review one maker's output alone.
            await self._audit(
                task,
                "RISK_REVIEW_MAKER_OUTPUTS_INCOMPLETE",
                {
                    "hasUnderwriting": outputs.underwriting is not None,
                    "hasLegal": outputs.legal is not None,
                },
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "independent risk review requires both maker handoffs "
                "(READY_FOR_RISK_REVIEW from underwriting AND legal/compliance)",
            )
        underwriting = outputs.underwriting
        legal = outputs.legal
        assert underwriting is not None and legal is not None
        assert outputs.underwriting_execution_id is not None
        assert outputs.legal_execution_id is not None

        run = CheckerRunContext(
            task_id=task.id,
            execution_id=self._execution_id_factory(),
            correlation_id=f"risk-review:{task.id}",
        )
        if run.execution_id in (outputs.underwriting_execution_id, outputs.legal_execution_id):
            # Same-execution guard: fail closed BEFORE any model call.
            await self._audit(
                task,
                "RISK_REVIEW_SAME_EXECUTION_GUARD_TRIGGERED",
                {
                    "checkerExecutionId": str(run.execution_id),
                    "underwritingExecutionId": str(outputs.underwriting_execution_id),
                    "legalExecutionId": str(outputs.legal_execution_id),
                },
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "checker execution id must differ from every reviewed maker execution id "
                "(maker-checker separation)",
            )
        await save_checkpoint(
            CHECKPOINT_MAKER_OUTPUTS,
            {
                "underwritingAssessmentId": str(underwriting.id),
                "legalAssessmentId": str(legal.id),
            },
        )

        open_gaps = await self._repository.load_open_gaps(task.case_id, task.case_version)
        pre_analysis = compute_deterministic_pre_analysis(
            underwriting=underwriting, legal=legal, checker_view=view, open_gaps=open_gaps
        )
        universe = build_target_universe(underwriting, legal)
        await save_checkpoint(
            CHECKPOINT_PRE_ANALYSIS,
            {
                "deterministicChallengeCount": len(pre_analysis.all_challenges),
                "blockingGapVisibilityCount": len(pre_analysis.visibility_checks.blocking_gaps),
                "exceptionVisibilityCount": len(pre_analysis.visibility_checks.exceptions),
            },
        )

        try:
            assessment = await self._inference.infer(
                view=view,
                underwriting=underwriting,
                underwriting_execution_id=outputs.underwriting_execution_id,
                legal=legal,
                legal_execution_id=outputs.legal_execution_id,
                pre_analysis=pre_analysis,
                universe=universe,
                policy_hits=legal.policy_hits,
                controlled_check_results=legal.controlled_check_results,
                run=run,
            )
        except InferenceUnavailableError:
            await self._audit(
                task,
                "RISK_REVIEW_GATEWAY_UNAVAILABLE",
                {"reason": "FPT reasoning endpoint unavailable"},
            )
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW, "FPT reasoning endpoint unavailable"
            )
        except SameExecutionGuardTriggered as exc:
            # Second-layer guard (also enforced pre-inference above and by the
            # domain schema itself): never retryable, always manual review.
            await self._audit(
                task, "RISK_REVIEW_SAME_EXECUTION_GUARD_TRIGGERED", {"reason": str(exc)[:2000]}
            )
            return StageResult(WorkerOutcome.FAILED_MANUAL_REVIEW, str(exc))
        except (CheckerOutputInvalid, InferenceError, ValidationError) as exc:
            await self._audit(
                task, "RISK_REVIEW_OUTPUT_REJECTED", {"reason": str(exc)[:2000]}
            )
            return StageResult(WorkerOutcome.RETRY_WAIT, f"checker output rejected: {exc}")

        await save_checkpoint(
            CHECKPOINT_INFERENCE, {"assessment": assessment.model_dump(mode="json")}
        )
        return await self._persist_stage(task, assessment, save_checkpoint)

    async def _persist_stage(
        self,
        task: TaskRecord,
        assessment: RiskReviewAssessment,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        persisted = await persist_checker_output(
            self._repository, assessment, handoff_id=self._handoff_id_for(assessment)
        )
        await self._save_persisted_checkpoint(save_checkpoint, persisted)
        await self._audit(
            task,
            "RISK_REVIEW_ASSESSMENT_PERSISTED",
            {
                "assessmentId": str(persisted.assessment_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "challengeCount": len(persisted.challenge_ids),
                "executionId": str(assessment.provenance.execution_id),
            },
        )
        return StageResult()

    @staticmethod
    def _handoff_id_for(assessment: RiskReviewAssessment) -> UUID:
        return uuid5(NAMESPACE_OID, f"risk-review-handoff:{assessment.id}")

    @staticmethod
    def _assessment_from_checkpoint(checkpoint: TaskCheckpoint) -> RiskReviewAssessment:
        raw = checkpoint.checkpoint_data.get("assessment")
        if not isinstance(raw, Mapping):
            raise CheckerOutputInvalid("inference checkpoint has no assessment payload")
        return RiskReviewAssessment.model_validate(raw)

    async def _save_persisted_checkpoint(
        self, save_checkpoint: CheckpointCallback, persisted: PersistedCheckerOutput
    ) -> None:
        await save_checkpoint(
            CHECKPOINT_PERSISTED,
            {
                "assessmentId": str(persisted.assessment_id),
                "handoffId": str(persisted.handoff_id),
                "handoffState": persisted.handoff_state,
                "challengeIds": [str(cid) for cid in persisted.challenge_ids],
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
                    "role": RISK_REVIEW_AGENT_ROLE,
                    "recordedAt": self._clock().isoformat(),
                    **dict(event_data),
                },
            )
        )
