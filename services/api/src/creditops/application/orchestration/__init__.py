"""Deterministic Case Orchestrator (ADR-0001).

The LLM planner only PROPOSES; this package's deterministic task-graph engine
DECIDES.  Dependency ordering, readiness, human gates, stale-version fencing,
idempotency, and bounded retries are enforced here and in database constraints.
The orchestrator coordinates work; it never writes facts, findings, or
specialist conclusions and never resolves gaps, conflicts, or challenges.
"""
