# Project Context

## Identity

**CONFIRMED — Project name:** SHB CreditOps EvidenceGraph.

**CONFIRMED — Working description:** A verifiable multi-agent AI system that supports preparation and review of SME corporate credit applications.

The intended system converts fragmented customer documents into a structured credit case, detects missing evidence and inconsistencies, supports controlled specialist analysis, retrieves applicable policies, suggests additional documents, enables independent risk challenge, and prepares a draft credit proposal for human consideration. Human approval is required before any operational action.

This is not a generic chatbot. Its central object is the [Credit Case Digital Twin](DOMAIN_MODEL.md), and chat history must not be the source of truth.

Conceptual flow:

```text
Customer documents
  -> structured case data
  -> specialist analysis
  -> evidence gaps
  -> additional-document suggestions
  -> independent risk challenge
  -> human review
  -> controlled operational action
```

## Challenge context

**CONFIRMED:** The project responds to SHB's challenge, “Digital Expert Agents – A Team of AI Specialists for Banking Operations.”

**PROPOSED — eventual demonstration capabilities:**

- multiple specialist AI agents with distinct responsibilities and permissions;
- planner–executor orchestration and shared workflow state;
- domain-specific retrieval-augmented generation (RAG);
- tool and function calling;
- collaboration between banking specialist roles;
- controlled actions through mock banking systems;
- dashboard traces, provenance, and auditability; and
- comparison with a single-agent chatbot baseline.

These are requirements and design directions, not implemented capabilities.

## Users and authority

**ASSUMPTION:** Expected users include relationship/intake staff, credit underwriters, legal/compliance/collateral reviewers, independent risk reviewers, credit operations staff, and human approvers. Exact SHB role names, responsibilities, and approval authorities remain an [open question](OPEN_QUESTIONS.md).

**CONFIRMED:** Human banking employees retain authority for material decisions and approvals. See [Product Boundaries](PRODUCT_BOUNDARIES.md).

## Initial banking use case

**CONFIRMED:** The first use case is preparation of SME working-capital credit applications after a corporate customer has expressed a financing need. Example purposes include purchasing materials or inventory, financing short-term operations, and bridging supplier payment and customer collection cycles.

**CONFIRMED — first-phase focus:** stages 2–6 of the corporate-credit workflow: understand the need, collect and check documents, assess the customer and request, prepare a proposed structure, and support independent review and human approval. Details are in [Banking Workflow](BANKING_WORKFLOW.md).

**PROPOSED — later phases:** post-approval operations and post-credit monitoring. These are not part of the initial phase.

## Prototype-oriented customer document model

**ASSUMPTION:** A corporate customer may provide the following document groups. This is a prototype-oriented model and is **not** an official SHB checklist.

| Group | Illustrative documents |
|---|---|
| Legal | Enterprise registration certificate; company charter; legal representative identification; appointment or authorization decisions; ownership/shareholder information; operating licences |
| Credit request | Loan application; requested amount, purpose, term, and expected use date; repayment source; proposed collateral |
| Business | Business and working-capital plans; purchase/sales contracts; purchase orders; invoices; supplier/customer information; inventory or procurement plans |
| Financial | Financial statements; income statement; balance sheet; cash-flow statement; tax declarations; bank statements; receivable/payable ageing; existing debt schedule |
| Collateral | Ownership or usage certificates; vehicle registration; deposit documents; valuation reports; purchase agreements; other evidence of legal ownership |

The official checklist, document validity rules, and stage-specific requirements must be supplied or confirmed by SHB before being treated as banking rules.

## Current status

- **CONFIRMED:** The business concept and broad corporate-credit workflow have been defined.
- **CONFIRMED:** Six conceptual agent roles and Evidence Gap Resolution have been defined.
- **CONFIRMED:** The target managed architecture is Vercel for the frontend, Cloud Run for the FastAPI API and asynchronous `creditops-worker` Job, Supabase for durable state, queues, object storage, and retrieval metadata, and FPT AI Factory for managed inference only.
- **CONFIRMED:** The earlier hourly FPT H100 VM and local vLLM direction is superseded and retained only as decision history.
- **CONFIRMED:** A local walking-skeleton prototype is implemented: Vietnamese case/intake flows, assigned-officer access, private Supabase upload intents and completion verification, durable identifier-only queue contracts, worker-slot leases with checkpointed retry/redelivery semantics, Cloud Run dispatch contracts, safe document parsing, and an FPT capability gateway.
- **CONFIRMED:** The prototype is not a deployed banking system. Supabase, Cloud Run, Vercel, and FPT endpoints remain unprovisioned in this workspace; the worker refuses to run without injected real dependencies, and FPT live smoke tests remain skipped until exact managed endpoint configuration and credentials are supplied.
- **CONFIRMED:** None of the target managed services has been provisioned or deployed.
- **OPEN QUESTION:** No final model has passed the required Vietnamese banking and document benchmarks.
- **OPEN QUESTION:** No official SHB policy corpus, checklist, workflow configuration, credit memo template, or API sandbox is currently available.

## Managed application and inference direction

**CONFIRMED:** Durable product state and model inference are separate concerns. Supabase stores the authoritative Credit Case Digital Twin, EvidenceGraph records, workflow checkpoints, queue messages, retrieval metadata, audit records, and immutable document objects. Cloud Run executes the FastAPI application, deterministic state machine, authorized tools, and asynchronous processing workers. FPT AI Factory provides managed inference endpoints and is never the source of truth for a case.

The approved separation is:

```text
Vercel frontend
  -> Cloud Run application API and workers
  -> Supabase durable state, queues, storage, and pgvector
  -> controlled FPT model gateway
  -> FPT managed inference endpoints
```

**CONFIRMED:** The frontend communicates with the application API and never calls FPT endpoints directly. Documents use a backend-created upload intent with a signed or authenticated resumable direct upload to private Supabase Storage rather than passing through Vercel Functions. Queue messages contain identifiers, not document bodies or secrets.

**PROPOSED:** The six logical agent roles may use a shared benchmark-selected reasoning endpoint plus task-specific managed document, vision, embedding, and optional reranking endpoints. Specialization remains defined by instructions, tools, permissions, schemas, and scoped evidence rather than a dedicated model per role. Final selection is driven by Vietnamese banking quality, structured-output and tool-calling reliability, citation grounding, latency, throughput, provider availability, data controls, and cost.

## Data principles

**CONFIRMED:** Development and demonstration must use synthetic data only. Every demonstration must visibly state:

> All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.

Synthetic policies must not be described as official SHB policies. Real personal or banking information must not be used.

**PROPOSED:** The eventual synthetic case set should include complete, missing-document, conflicting-data, policy-exception, poor-scan, and manual-review cases.

## High-level future phases

This outline expresses sequencing only, not an implementation plan or commitment.

1. **CONFIRMED — Context foundation:** maintain domain, governance, workflow, and decision documentation.
2. **PROPOSED — Pre-approval demonstrator:** support stages 2–6 with synthetic cases and mock services.
3. **PROPOSED — Controlled post-approval extension:** explore approved operational steps through mock systems.
4. **PROPOSED — Monitoring extension:** explore post-credit monitoring through the optional Monitoring and Recovery Agent.

## Related documents

- [Banking Workflow](BANKING_WORKFLOW.md)
- [Agent Architecture](AGENT_ARCHITECTURE.md)
- [Evidence Gap Resolution](EVIDENCE_GAP_RESOLUTION.md)
- [Domain Model](DOMAIN_MODEL.md)
- [Technical Direction](TECHNICAL_DIRECTION.md)
- [Product Boundaries](PRODUCT_BOUNDARIES.md)
- [Open Questions](OPEN_QUESTIONS.md)
