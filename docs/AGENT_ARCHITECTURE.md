# Agent Architecture

## Status and intent

**CONFIRMED:** The approved master design ([full credit-lifecycle design](superpowers/specs/2026-07-18-full-credit-lifecycle-agent-workflow-design.md), section 7.2) defines eight logical agent roles for the 14-stage lifecycle: Case Orchestrator, Relationship & Intake, Credit Underwriting, Legal/Compliance & Collateral, Independent Risk Review, Credit Operations, Post-Credit Monitoring, and Collections & Recovery. Evidence Gap Resolution remains a shared workflow capability, not a ninth agent. The six roles detailed below are the pre-approval subset exercised by the current local walking skeleton; Post-Credit Monitoring and Collections & Recovery activate only in the post-credit waves and follow the same boundaries. Roles describe responsibilities, information access, tools, output contracts, and permissions; they do not require one language model or service per role.

**PROPOSED:** A benchmark-selected managed reasoning endpoint may support multiple roles through role-specific instructions, tools, data access, permissions, and structured-output schemas. Task-specific managed KIE, table, vision, embedding, and optional reranking endpoints may be used where benchmarks justify them. The roles are application-level responsibilities, not separate model servers.

## Agent and model-serving separation

**CONFIRMED:** All model calls pass through the Cloud Run backend's provider-neutral gateway. The initial design is:

```text
specialist agent role
  -> durable task and checkpoint in Supabase
  -> Cloud Run worker
  -> controlled FPT managed endpoint
  -> structured response or tool request
  -> backend validates and executes any authorized tool
  -> persisted workflow continues
```

FPT AI Factory provides inference only. It is not an agent, workflow engine, approval service, tool executor, or source of truth for case state. Selecting different managed endpoints for reasoning, KIE, tables, vision, embeddings, or reranking does not create additional application-level agents.

## Core roles

| Role | Primary responsibility | Must not |
|---|---|---|
| Case Orchestrator | Plan and route case work, track dependencies, and stop at human gates | Perform specialist analysis or make a credit decision |
| Relationship and Intake Agent | Structure the financing need and check initial document completeness | Invent missing customer facts or send unapproved requests |
| Credit Underwriting Agent | Prepare evidence-backed business, financial, cash-flow, and structure analysis | Approve/reject credit or replace deterministic calculations |
| Legal, Compliance and Collateral Agent | Review legal status, authority, controlled checks, policy applicability, and collateral-document completeness | Make final legal determinations or independently value collateral with an LLM |
| Independent Risk Review Agent | Challenge the maker's analysis, assumptions, mitigants, gaps, and exceptions | Act as the underwriting maker or approve/reject credit |
| Credit Operations Agent | Assemble the evidence package, draft the memo, and prepare controlled actions | Execute sensitive actions without explicit human authorization |

### Case Orchestrator

The orchestrator should eventually:

- understand the current structured case state;
- create and update a task plan;
- assign work to appropriate specialists;
- track dependencies and affected analyses;
- route work back when information is missing;
- surface deadlocks, uncertainty, and unresolved gaps; and
- pause at required human approval points.

It coordinates work but does not make specialist or credit conclusions. Its plan is workflow state, not a substitute for evidence.

### Relationship and Intake Agent

The intake role should eventually:

- capture requested amount, purpose, term, expected use date, proposed repayment source, and proposed collateral;
- associate received documents with the case;
- identify missing, duplicate, expired, inconsistent, or low-confidence material;
- structure the initial request for specialist review; and
- create evidence-gap suggestions for human consideration.

It must not fill missing fields with unsupported assumptions or communicate an additional-document request without human approval.

### Credit Underwriting Agent

The underwriting maker should eventually:

- assess the business model, activities, industry, customers, and suppliers;
- analyze financial performance, cash flow, and working-capital need;
- assess the proposed primary repayment source and downside scenarios;
- propose a preliminary financing structure;
- identify risks, mitigants, assumptions, and evidence gaps; and
- cite the evidence and deterministic calculations behind material findings.

Material arithmetic, ratios, projections, and rule checks should be produced by controlled tools and referenced by the agent. The role prepares analysis; it does not make the credit decision.

### Legal, Compliance and Collateral Agent

This specialist role should eventually:

- review corporate legal status and representative or signatory authority;
- identify ownership inconsistencies;
- retrieve potentially applicable policies with exact citations;
- interpret controlled KYC, AML, related-party, and watchlist tool results;
- review collateral-document ownership and legal completeness; and
- surface possible policy, legal, or collateral exceptions for human review.

The role must distinguish a potential issue from a legal conclusion. It must not perform production compliance checks, declare wrongdoing, waive policy, or generate an LLM-only collateral value.

### Independent Risk Review Agent

The risk checker should eventually:

- independently inspect the case and maker analysis;
- challenge unsupported assumptions and conclusions;
- identify omitted material risks;
- test whether proposed mitigants address the stated risks;
- verify that exceptions and blocking gaps remain visible; and
- request information, structural changes, manual review, or escalation.

**CONFIRMED:** The checker must remain separate from the underwriting maker. It may recommend further review but must not approve or reject credit.

### Credit Operations Agent

The operations role should eventually:

- check conceptual package completeness;
- assemble evidence and provenance references;
- consolidate human-approved additional-document requests;
- prepare a draft credit proposal or credit memo; and
- prepare proposed actions for later controlled mock-system workflows.

Any state-changing or operational action requires deterministic validation, authorization checks, an explicit human approval record, and an audit event. Real banking-system mutation is outside the current scope.

## Optional future role

**OUT OF SCOPE for the initial phase:** A Monitoring and Recovery Agent may later support covenant and payment monitoring, early-warning indicators, collateral monitoring, and escalation to debt-management staff. Its responsibilities, data access, and authority are not yet defined.

## Planner–executor model

**PROPOSED:** A planner–executor pattern may use the Case Orchestrator as planner and the specialist roles as bounded executors:

1. Read the versioned Credit Case Digital Twin.
2. Determine which tasks are ready, blocked, or invalidated.
3. Assign bounded work with an expected structured output.
4. Validate the output schema and evidence references.
5. Merge accepted results without erasing authorship or prior versions.
6. Route material findings to independent review.
7. Stop at the applicable human gate.

The planner may sequence work but may not expand an agent's permissions.

**PROPOSED:** The expected dependency-aware execution pattern is:

```text
Relationship and Intake
  -> Credit Underwriting and Legal/Compliance/Collateral may run in parallel
  -> Evidence Gap Resolution
  -> Independent Risk Review
  -> Credit Operations
```

The orchestrator must not start all agent roles concurrently for every case or permit unrestricted agent loops. Concurrency is bounded by case dependencies, role permissions, tool results, human gates, Cloud Run capacity, FPT endpoint quotas, and measured cost. Supabase Queues is the approved durable application-level work queue; provider-side inference scheduling remains an internal FPT concern.

## Agents versus deterministic tools

| Use agents for | Use deterministic tools or services for |
|---|---|
| Contextual interpretation | OCR and document parsing |
| Specialist analysis and explanation | Financial calculations and reconciliations |
| Task coordination | Explicit policy-rule evaluation |
| Evidence-backed challenge | KYC, AML, watchlist, and related-party lookups |
| Exception identification | Collateral valuation lookup |
| Suggested next steps | Database and workflow-state changes |
| Drafting for human review | Document generation and mock LOS/ACAS actions |

**CONFIRMED:** The design must avoid multi-agent theatre. A role is justified only where it has a distinct duty, context, tool set, output contract, or permission boundary.

## Maker–checker and separation-of-duties invariants

- The Credit Underwriting Agent is the maker; the Independent Risk Review Agent is the checker.
- The same role execution must not author and independently clear the same material conclusion.
- Checker comments, maker responses, unresolved disagreements, and human dispositions must remain traceable.
- Orchestration must not silently mark a challenge, gap, or exception resolved.
- Human authority is required for customer communication, exception disposition, the credit decision, and operational actions.

**OPEN QUESTION:** The official SHB role mapping, approval delegation, and whether additional independent legal or collateral checkers are required have not been supplied.

## Shared state and security principles

Agents should eventually operate on scoped views of the [Credit Case Digital Twin](DOMAIN_MODEL.md), not on unrestricted chat history. Uploaded documents are untrusted inputs; document text must not override system instructions, authorization rules, or tool policies.

The application backend must persist workflow and case state outside the model process. No model replica may hold the only durable copy of progress, approvals, evidence relationships, or audit history.

Every material output should record its agent role, execution identifier, input case version, evidence references, tool results, confidence or uncertainty, status, and timestamp. Access should follow least privilege and preserve an append-only audit trail.

## Related documents

- [Banking Workflow](BANKING_WORKFLOW.md)
- [Evidence Gap Resolution](EVIDENCE_GAP_RESOLUTION.md)
- [Domain Model](DOMAIN_MODEL.md)
- [Product Boundaries](PRODUCT_BOUNDARIES.md)
- [Technical Direction](TECHNICAL_DIRECTION.md)
