"""Planner: deterministic default plan plus an optional, validated LLM proposal.

ADR-0001 in force: the LLM only PROPOSES an ordering/priority over the tasks the
deterministic readiness engine has already declared READY.  Every proposal passes
a deterministic validator; an invalid proposal is rejected with recorded errors
and the engine falls back to the default plan derived from the canonical graph.
This is a rule-based fallback, never a model fallback: with no FPT endpoint the
default plan is used directly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.readiness import ReadinessReport
from creditops.application.ports.model_gateway import (
    InferenceError,
    InferenceGateway,
    ReasonRequest,
)
from creditops.domain.orchestration import TaskReadiness, TaskType

PROPOSAL_SCHEMA_VERSION = "orchestrator-proposal-v1"
PROMPT_VERSION = "orchestrator-prompt-v1"

ProposalStatus = Literal["ACCEPTED", "REJECTED", "SKIPPED"]
PlanSource = Literal["DEFAULT", "LLM_PROPOSED"]

# JSON schema handed to the reasoning endpoint.  ``additionalProperties: false``
# and the closed ``task_type`` enum mean a well-formed response cannot smuggle a
# gate flag, a conclusion, or an unknown task type past the gateway validator.
PROPOSAL_JSON_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["task_type", "priority"],
                "properties": {
                    "task_type": {
                        "type": "string",
                        "enum": [task_type.value for task_type in TaskType],
                    },
                    "priority": {"type": "integer", "minimum": 0, "maximum": 999},
                },
            },
        }
    },
}


class ProposedStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_type: TaskType
    priority: int = Field(ge=0, le=999)


class PlannerProposalPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: tuple[ProposedStep, ...] = Field(max_length=16)


@dataclass(frozen=True, slots=True)
class PlanStep:
    task_type: TaskType
    priority: int
    rationale: str


@dataclass(frozen=True, slots=True)
class Plan:
    steps: tuple[PlanStep, ...]
    source: PlanSource

    def task_types(self) -> tuple[TaskType, ...]:
        return tuple(step.task_type for step in self.steps)


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    status: ProposalStatus
    plan: Plan
    validation_errors: tuple[str, ...]
    prompt_version: str
    schema_version: str
    model_version: str | None
    raw_proposal: Mapping[str, object] | None = None


class OrchestratorPrompt:
    """Load the versioned, Vietnamese trusted-instruction prompt from disk."""

    version = PROMPT_VERSION

    def __init__(self, text: str | None = None) -> None:
        self._text = text if text is not None else self._load()

    @staticmethod
    def _load() -> str:
        return (
            resources.files("creditops.prompts.orchestrator")
            .joinpath("v1.md")
            .read_text(encoding="utf-8")
        )

    @property
    def text(self) -> str:
        return self._text


def build_default_plan(report: ReadinessReport, template: DependencyTemplate) -> Plan:
    ready = report.ready_types()
    ordered = sorted(ready, key=template.priority_of)
    steps = tuple(
        PlanStep(task_type, template.priority_of(task_type), "canonical default order")
        for task_type in ordered
    )
    return Plan(steps=steps, source="DEFAULT")


def validate_proposal(
    payload: PlannerProposalPayload,
    report: ReadinessReport,
    template: DependencyTemplate,
) -> tuple[str, ...]:
    """Reject unknown types, dependency/gate violations, and duplicates.

    A single deterministic check — "every proposed task type must be READY per
    the engine" — simultaneously rejects gate bypass, dependency violations, and
    tasks blocked by a blocking evidence gap: none of those are ever READY.
    """

    errors: list[str] = []
    state = report.state_map()
    seen: set[TaskType] = set()
    known = set(template.ordered_types())
    for step in payload.steps:
        if step.task_type not in known:
            errors.append(f"unknown task type proposed: {step.task_type.value}")
            continue
        if step.task_type in seen:
            errors.append(f"duplicate task type proposed: {step.task_type.value}")
            continue
        seen.add(step.task_type)
        if state.get(step.task_type) is not TaskReadiness.READY:
            errors.append(
                f"proposed task {step.task_type.value} is not READY "
                f"({state.get(step.task_type, TaskReadiness.BLOCKED).value})"
            )
    return tuple(errors)


def _plan_from_proposal(
    payload: PlannerProposalPayload,
    report: ReadinessReport,
    template: DependencyTemplate,
) -> Plan:
    # Order strictly by the planner's priority, then canonical order as a stable
    # tie-break.  Any READY type the planner omitted is appended in canonical
    # order so the planner may reprioritise but can never drop required work.
    proposed = {step.task_type: step.priority for step in payload.steps}
    ready = report.ready_types()
    ordered = sorted(
        ready,
        key=lambda task_type: (
            proposed.get(task_type, 1_000),
            template.priority_of(task_type),
        ),
    )
    steps = tuple(
        PlanStep(
            task_type,
            proposed.get(task_type, template.priority_of(task_type)),
            "llm-proposed priority" if task_type in proposed else "appended ready task",
        )
        for task_type in ordered
    )
    return Plan(steps=steps, source="LLM_PROPOSED")


class OrchestrationPlanner:
    def __init__(
        self,
        template: DependencyTemplate,
        *,
        gateway: InferenceGateway | None = None,
        prompt: OrchestratorPrompt | None = None,
    ) -> None:
        self._template = template
        self._gateway = gateway
        self._prompt = prompt or OrchestratorPrompt()

    async def plan(
        self,
        *,
        case_id: UUID,
        correlation_id: str,
        report: ReadinessReport,
    ) -> ProposalOutcome:
        default = build_default_plan(report, self._template)
        ready = report.ready_types()
        if self._gateway is None or not ready:
            return ProposalOutcome(
                status="SKIPPED",
                plan=default,
                validation_errors=(),
                prompt_version=self._prompt.version,
                schema_version=PROPOSAL_SCHEMA_VERSION,
                model_version=None,
            )

        try:
            result = await self._gateway.reason(
                self._build_request(case_id, correlation_id, ready)
            )
            payload = PlannerProposalPayload.model_validate(result.payload)
        except (InferenceError, ValidationError) as exc:
            return ProposalOutcome(
                status="REJECTED",
                plan=default,
                validation_errors=(f"planner proposal unavailable or invalid: {exc}",),
                prompt_version=self._prompt.version,
                schema_version=PROPOSAL_SCHEMA_VERSION,
                model_version=None,
            )

        raw = payload.model_dump(mode="json")
        errors = validate_proposal(payload, report, self._template)
        if errors:
            return ProposalOutcome(
                status="REJECTED",
                plan=default,
                validation_errors=errors,
                prompt_version=self._prompt.version,
                schema_version=PROPOSAL_SCHEMA_VERSION,
                model_version=result.model_id,
                raw_proposal=raw,
            )
        return ProposalOutcome(
            status="ACCEPTED",
            plan=_plan_from_proposal(payload, report, self._template),
            validation_errors=(),
            prompt_version=self._prompt.version,
            schema_version=PROPOSAL_SCHEMA_VERSION,
            model_version=result.model_id,
            raw_proposal=raw,
        )

    def _build_request(
        self,
        case_id: UUID,
        correlation_id: str,
        ready: Sequence[TaskType],
    ) -> ReasonRequest:
        # Identifiers and readiness only — never a document body or finding.
        context = json.dumps(
            {"caseId": str(case_id), "readyTaskTypes": [t.value for t in ready]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ReasonRequest(
            correlation_id=correlation_id,
            case_id=case_id,
            content=context,
            response_schema=PROPOSAL_JSON_SCHEMA,
            system_context=self._prompt.text,
        )
