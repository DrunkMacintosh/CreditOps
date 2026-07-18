# SHB CreditOps EvidenceGraph

A verifiable multi-agent system for preparing and reviewing SME working-capital credit cases. The central object is the Credit Case Digital Twin — structured, versioned, evidence-traceable case state — not a chat history. All data is synthetic.

## Language

### Case state

**Credit Case Digital Twin**:
The versioned, structured record of a credit case: facts, evidence, gaps, conflicts, tasks, handoffs, and audit trail. The single source of truth for case work.
_Avoid_: chat history, conversation, session

**Case Version**:
The optimistic-concurrency version of a case. Every material output and task binds to the case version it read; a stale version cannot write newer state.

**Candidate Fact**:
A fact proposed by extraction or an agent, grounded in a document region, awaiting human disposition. Not authoritative.

**Confirmed Fact**:
A candidate fact the assigned officer accepted or corrected. The only fact class agents may treat as authoritative input.
_Avoid_: extracted fact (ambiguous)

**Evidence Edge**:
A typed link between two case entities (fact, document region, finding, challenge) that carries provenance.

**Evidence Gap**:
A recorded absence of required evidence. Lifecycle: PROVISIONAL → FORMAL → RESOLVED (or STALE); blocking level BLOCKING, CONDITIONAL, or CLARIFICATION.
_Avoid_: missing data, TODO

**Evidence Conflict**:
Two pieces of evidence that cannot both be true, preserved with both interpretations until resolved by a human.

**Handoff**:
An immutable, case-version-bound package one role produces for the next role to consume.

### Orchestration

**Task**:
A durable unit of work with a finite type, lease, attempt count, idempotency key, and case/document version references. Queue messages carry task identifiers only.

**Task Graph**:
The dependency-aware set of tasks for a case, with readiness derived deterministically from dependencies, gaps, gates, and case version.

**Checkpoint**:
A sequenced, durable record of partial task progress; retries resume from the latest valid checkpoint.

**Execution**:
One run of an agent role against a task, identified by an execution id recorded on every material output.

**Human Gate**:
A workflow point where progression halts until an authorized human records a disposition. Gates cannot be bypassed by any agent, plan, or retry.
_Avoid_: approval step (vague)

**Planner Proposal**:
A schema-validated task-plan suggestion from the LLM planner. Advisory only; the deterministic engine accepts or rejects it (ADR-0001).

### Roles and review

**Maker**:
The Credit Underwriting Agent: prepares evidence-backed analysis. Never approves or rejects credit.

**Checker**:
The Independent Risk Review Agent: challenges the maker's output. Never edits maker output, never resolves its own challenges.

**Challenge**:
A checker finding that disputes a maker conclusion, assumption, or omission. Always evidence-referenced; persists until a human disposition.

**Disposition**:
An authorized human's recorded decision on a challenge, gap, exception, or proposed action.
_Avoid_: resolution (agents cannot resolve)

**Exception**:
A potential deviation from policy surfaced for human review. Agents surface exceptions; only humans decide them.

### Operations

**Proposed Action**:
A controlled action drafted by the Credit Operations Agent that executes only after deterministic validation plus an explicit human authorization record.

**Credit Memo**:
The draft credit proposal assembled from confirmed facts, findings, challenges, and their provenance. A draft for human decision-makers, never a decision.

**Policy Corpus**:
The versioned, checksummed set of policy documents retrieval runs against. Currently synthetic and labelled as such (ADR-0002); never described as official SHB policy.

**Upload Intent**:
A short-lived backend record authorizing one direct browser upload to private storage, verified before document registration.
