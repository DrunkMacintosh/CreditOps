"""Orchestration domain vocabulary: task types, human gates, derived readiness.

These enums name the finite sets the deterministic task-graph engine reasons
over.  Per ADR-0001 the engine — not the LLM planner — is the sole authority
over state.  Readiness is DERIVED from dependency completion, blocking evidence
gaps, human-gate status, and case-version currency; it is never stored as a
mutable column.  Queue messages carry only identifiers, so nothing here holds a
document body, finding, or secret.
"""

from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    """The finite set of durable task types the engine may schedule.

    ``DOCUMENT_INGESTION`` is the pre-existing per-document pipeline task and
    remains the backward-compatible default for any queue envelope that omits a
    ``task_type`` field.
    """

    DOCUMENT_INGESTION = "DOCUMENT_INGESTION"
    ORCHESTRATOR_PLAN = "ORCHESTRATOR_PLAN"
    CREDIT_UNDERWRITING = "CREDIT_UNDERWRITING"
    LEGAL_COMPLIANCE_COLLATERAL = "LEGAL_COMPLIANCE_COLLATERAL"
    INDEPENDENT_RISK_REVIEW = "INDEPENDENT_RISK_REVIEW"
    CREDIT_OPERATIONS = "CREDIT_OPERATIONS"


class GateType(StrEnum):
    """Human approval points that block downstream task readiness.

    ASSUMPTION: these gate names are SYNTHETIC.  The official SHB role mapping,
    approval delegation, and whether additional independent legal or collateral
    checkers are required are OPEN QUESTIONS (docs/AGENT_ARCHITECTURE.md).  No
    gate here is presented as an official SHB control.

    - ``G1_INTAKE_COMPLETE``   satisfied deterministically by the intake handoff.
    - ``G2_GAP_REQUEST_APPROVAL`` formal evidence-gap / customer-request approval.
    - ``G3_RISK_DISPOSITION``  human disposition of the checker's challenges.
    - ``G4_OPS_AUTHORIZATION``  human authorization of the proposed actions/memo.
    - ``HG_FINANCING_NEED_CONFIRMED`` stage-2 confirmation of the versioned
      financing request (master design section 5 stage 2).  PROPOSED synthetic
      gate name; no official SHB mapping.  Human-satisfied only, like G2/G3/G4.
    - ``HG_UNDERWRITING_ASSESSMENT_REVIEWED`` stage-4 human review of the maker's
      underwriting assessment (master design section 5 giai đoạn 4).
    - ``HG_LEGAL_ASSESSMENT_REVIEWED`` stage-4 human review of the legal /
      compliance / collateral assessment (master design section 5 giai đoạn 4).
    - ``HG_MAKER_SUBMISSION_CONFIRMED`` stage-5 confirmation that the human
      underwriter/maker submits the credit proposal (master design section 5
      giai đoạn 5).  PROPOSED synthetic gate names; no official SHB mapping.
      Human-satisfied only, like G2/G3/G4.

    Only ``G1`` may be satisfied by the engine (from the intake handoff).  Every
    other gate is satisfied exclusively by an authorized human disposition; no
    agent, plan, retry, or duplicate delivery may satisfy or bypass it.

    NB (PROPOSED): ``HG_FINANCING_NEED_CONFIRMED`` and the three stage-4/5
    specialist/maker gates are NOT required_gate on any task-graph node
    (application/orchestration/graph.py).  For now they are recorded human state
    surfaced to their respective role surfaces; whether downstream readiness
    should later REQUIRE them is a deferred decision, not wired here.
    """

    G1_INTAKE_COMPLETE = "G1_INTAKE_COMPLETE"
    G2_GAP_REQUEST_APPROVAL = "G2_GAP_REQUEST_APPROVAL"
    G3_RISK_DISPOSITION = "G3_RISK_DISPOSITION"
    G4_OPS_AUTHORIZATION = "G4_OPS_AUTHORIZATION"
    HG_FINANCING_NEED_CONFIRMED = "HG_FINANCING_NEED_CONFIRMED"
    HG_UNDERWRITING_ASSESSMENT_REVIEWED = "HG_UNDERWRITING_ASSESSMENT_REVIEWED"
    HG_LEGAL_ASSESSMENT_REVIEWED = "HG_LEGAL_ASSESSMENT_REVIEWED"
    HG_MAKER_SUBMISSION_CONFIRMED = "HG_MAKER_SUBMISSION_CONFIRMED"


class GateStatus(StrEnum):
    OPEN = "OPEN"
    SATISFIED = "SATISFIED"


class TaskReadiness(StrEnum):
    """Derived per-node state in the task graph; never persisted as a column."""

    BLOCKED = "BLOCKED"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"
