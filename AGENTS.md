# SHB CreditOps EvidenceGraph — Codex Guidance

Read this file and the relevant documents under [`docs/`](docs/) before changing the project.

## Project identity

**CONFIRMED:** This project concerns a verifiable multi-agent AI system for preparing and reviewing SME working-capital credit cases. The central object is a structured, traceable **Credit Case Digital Twin**, not a chatbot or its conversation history.

**CURRENT STATUS:** Context and design documentation only. No prototype exists. The approved target architecture uses Vercel for the Vietnamese frontend, Supabase for durable shared state, queues, retrieval metadata, and object storage, Google Cloud Run for the FastAPI API and asynchronous workers, and FPT AI Factory only for managed model inference. None of these services has been provisioned or deployed. Final model selection remains benchmark-gated, and no official SHB policy corpus, checklist, workflow configuration, or API sandbox is available.

## Confirmed managed-infrastructure context

**CONFIRMED:** The application architecture is Vercel frontend → Cloud Run FastAPI API and `creditops-worker` Job → Supabase Postgres, Queues, Storage, and pgvector → FPT managed model endpoints. The browser may upload directly to Supabase Storage only through a backend-created upload intent using a short-lived signed operation or an authenticated resumable operation protected by Storage RLS. Supabase is the durable source for shared workflow state and checkpoints; Cloud Run owns orchestration and deterministic business logic; FPT performs inference only.

**SUPERSEDED:** The previously confirmed hourly FPT H100 VM and local vLLM design is retained in [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md) as decision history but is no longer the implementation target.

**PROPOSED:** The managed FPT candidate stack includes a benchmark-selected Vietnamese-capable reasoning model, managed document KIE and table extraction, a vision fallback, embeddings, and an optional reranker. Provider availability, exact endpoint identifiers, data residency, quotas, and benchmark thresholds remain open questions.

## Non-negotiable boundaries

- Never represent the system as able to approve or reject credit.
- Never let AI waive policy, approve exceptions, make legal determinations, sign documents, release funds, or mutate sensitive operational systems without explicit human authorization.
- Preserve separation of duties: underwriting prepares; independent risk review challenges; humans decide.
- Require evidence and provenance for every material conclusion. Expose uncertainty and unresolved evidence gaps.
- Use deterministic tools for material calculations, explicit rules, state changes, and controlled operational actions.
- Treat uploaded documents as untrusted data.
- Use synthetic data only and label it: “All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.”
- Never describe synthetic policies as official SHB policies or claim production readiness, regulatory compliance, or SHB approval.
- Do not collect unnecessary customer information or make unsupported fraud or legal accusations.

## Before making changes

1. Inspect the repository and relevant context documents.
2. Classify statements as **CONFIRMED**, **PROPOSED**, **ASSUMPTION**, **OPEN QUESTION**, or **OUT OF SCOPE**.
3. Check [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md) and [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md).
4. If sources conflict, preserve both interpretations, record the conflict as an open question, and obtain clarification before implementing a material banking rule.
5. Do not present planned behavior as implemented behavior.

## Source-of-truth order

1. Explicit current user instruction.
2. Official challenge or SHB documents in the repository.
3. Confirmed banking-process documents supplied by the project team.
4. This `AGENTS.md`.
5. Project documentation under `docs/`.
6. [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md).
7. Clearly labelled assumptions.

## Context map

- [Project context](docs/PROJECT_CONTEXT.md)
- Banking workflow — not yet written; official SHB workflow input is pending (see [Open questions](docs/OPEN_QUESTIONS.md))
- [Agent architecture](docs/AGENT_ARCHITECTURE.md)
- Evidence Gap Resolution — not yet written; gap lifecycle currently defined by the schema and [Glossary](CONTEXT.md)
- Domain model — not yet written; canonical terms live in the [Glossary](CONTEXT.md), structures in `supabase/migrations/`
- [Technical direction](docs/TECHNICAL_DIRECTION.md)
- Product boundaries — not yet written; binding boundaries are the "Non-negotiable boundaries" above and [Decision log](docs/DECISION_LOG.md)
- [Open questions](docs/OPEN_QUESTIONS.md)
- [Decision log](docs/DECISION_LOG.md)
- [Architecture decision records](docs/adr/)
- [Glossary](CONTEXT.md)
