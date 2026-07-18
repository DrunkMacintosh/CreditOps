# Case Orchestrator: LLM proposes, deterministic engine decides

The Case Orchestrator uses an LLM planner (via the FPT `reason` capability, schema-validated) to propose task plans, routing, and priorities — but a deterministic task-graph engine remains the sole authority over state. Dependency ordering, readiness/blocked/superseded transitions, human gates, stale-case-version fencing, idempotency, and bounded retries are enforced in application code and database constraints; every LLM proposal passes through a deterministic validator, and an invalid proposal is rejected with an audit event, never applied. When no FPT endpoint is configured, the orchestrator still operates on a deterministic default plan derived from the canonical dependency graph (this is a rule-based fallback plan, not a model fallback).

## Considered Options

- **Fully deterministic planner (no LLM)** — rejected by project direction 2026-07-18: the planner should exercise the agentic pattern the specialist roles use, and plan quality can improve with case context.
- **LLM plan as authority** — rejected: conflicts with the confirmed decision "use deterministic tools for material calculations, explicit rules, state changes, and controlled actions" (docs/DECISION_LOG.md) and with the required orchestrator tests (human gates must hold even against a malformed plan).

## Consequences

All six required orchestrator test properties (dependency ordering, gap blocking, stale-version fencing, human-gate enforcement, duplicate-delivery idempotency, audit completeness) are testable without any model call, because the enforcement layer is model-free. The LLM planner is additive and cannot expand any agent's permissions.
