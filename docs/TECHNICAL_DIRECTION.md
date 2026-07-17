# Technical Direction

## Status

This document separates approved architecture from proposed provider configuration and unresolved controls.

**CONFIRMED:** A local walking-skeleton prototype now exercises the approved application boundaries. No cloud environment has been provisioned. The target architecture remains Vercel for the Vietnamese web interface, Google Cloud Run for the FastAPI API and an asynchronous `creditops-worker` Job, Supabase for durable shared state, queues, private object storage, retrieval indexes, and audit records, and FPT AI Factory only for managed model inference.

**SUPERSEDED:** The earlier hourly FPT H100 VM, local NVMe, vLLM, and locally hosted open-weight model design is retained in [Decision Log](DECISION_LOG.md) but is not the implementation target.

**OPEN QUESTION:** Provider regions, private connectivity, data residency, identity integration, backup settings, production-data authorization, exact FPT endpoint availability, and final model selection are unresolved.

## Approved application stack direction

| Layer | Direction | Status |
|---|---|---|
| Frontend | Next.js, React, TypeScript on Vercel | CONFIRMED architecture |
| Application API | Python, FastAPI, Pydantic on Cloud Run | CONFIRMED architecture |
| Workflow | Explicit deterministic state machine in the backend | CONFIRMED architecture |
| Durable jobs | Supabase Queues plus the Cloud Run `creditops-worker` Job | CONFIRMED architecture |
| Transactional state | Supabase PostgreSQL | CONFIRMED architecture |
| Object storage | Private Supabase Storage with immutable versioned keys | CONFIRMED architecture |
| Retrieval | PostgreSQL full-text search and pgvector; optional benchmark-gated reranking | CONFIRMED architecture |
| Evidence relationships | Typed relational edge records in PostgreSQL | CONFIRMED for the first release |
| Model gateway | Provider-neutral backend adapter with structured-output validation | CONFIRMED architecture |
| Model inference | FPT managed inference endpoints | CONFIRMED provider boundary; exact endpoints PROPOSED |

The frontend communicates with the Cloud Run API. It never calls FPT endpoints and never receives database service-role credentials or model secrets.

## Approved service architecture

```text
Assigned intake officer
  -> Vietnamese Next.js frontend on Vercel
  -> FastAPI API on Cloud Run
  -> Supabase PostgreSQL, Queues, Storage, and pgvector
  -> Cloud Run `creditops-worker` Job
  -> provider-neutral model gateway
  -> FPT managed inference endpoints
```

Documents do not pass through Vercel Functions. The API validates the intended object, records a short-lived `UploadIntent`, and returns either a signed upload operation for a normal upload or an authenticated resumable-upload contract protected by Storage RLS. The browser uploads directly to the private Supabase Storage object key. The API then verifies the stored object against the intent before registering a document version and enqueuing processing.

## Authority and source-of-truth boundaries

### Vercel frontend

The frontend:

- renders Vietnamese case, document, confirmation, conflict, gap, handoff, and audit views;
- submits user intent and human dispositions;
- performs direct signed or authenticated resumable uploads to Supabase Storage under a backend-created upload intent; and
- receives progress through bounded polling or authorized realtime updates.

It does not own workflow state, execute model calls, authorize tools, or contain privileged credentials.

### Cloud Run application API

The API owns:

- authentication-token validation and case-scoped authorization;
- assigned-intake-officer enforcement;
- deterministic state transitions and optimistic concurrency;
- signed-upload authorization and post-upload verification;
- task creation, idempotency keys, and queue dispatch;
- model routing, schema validation, and bounded retry policy;
- deterministic calculations and controlled tools;
- human approval gates; and
- append-only audit-event creation.

The API returns `202 Accepted` for asynchronous document work and never holds a browser request open for an entire document pipeline.

### Supabase

Supabase is the durable shared-state layer:

- PostgreSQL stores the Credit Case Digital Twin, EvidenceGraph, versioned facts, conflicts, gaps, tasks, checkpoints, retrieval metadata, permissions metadata, and audit records;
- Queues stores job identifiers, attempt metadata, and visibility leases;
- private Storage stores immutable originals and separately versioned derived artifacts; and
- pgvector stores embeddings linked to document, case, version, page, source region, authorization scope, and corpus version.

Queue messages contain opaque identifiers and processing metadata, not document bodies, extracted banking data, provider credentials, or free-form instructions.

Database backup must not be assumed to protect Storage objects. Object backup, versioning, restore testing, and retention require a separate approved design before real banking data is permitted.

### Cloud Run worker job

The `creditops-worker` Cloud Run Job performs asynchronous, idempotent processing. One execution claims and processes one queue task in the first release, matching the document-by-document test strategy:

1. claim a leased queue message;
2. verify the task and document version are still current;
3. load the immutable object through backend credentials;
4. validate and scan the file;
5. parse, OCR, classify, extract, embed, retrieve, and compare as required;
6. validate every deterministic and model output;
7. persist stage output and a checkpoint transactionally;
8. acknowledge the queue message only after durable success; and
9. emit audit and operational telemetry without unnecessary customer content.

The API requests a `creditops-worker` execution after enqueueing. Cloud Scheduler also requests one execution every minute as a recovery sweep, so a failed dispatch cannot strand work. An execution that finds no eligible message exits successfully. Cloud Run platform retries are disabled for the first release; attempts, bounded retry eligibility, and terminal states are recorded in Supabase. Parallel job executions may be enabled only after one-at-a-time correctness tests pass and provider limits are benchmarked.

### FPT AI Factory

FPT performs model inference only. It does not own agents, permissions, workflow, case state, tool execution, approvals, or audit authority. The backend sends the minimum scoped input necessary for a bounded task and records the configured endpoint, model identity, prompt version, schema version, latency, usage, and validation outcome.

There is no silent non-FPT fallback. An unavailable or invalid FPT response pauses the affected task visibly or routes it to manual review according to a deterministic failure policy.

## Durable workflow and checkpoints

Each asynchronous task has:

- a stable task identifier and idempotency key;
- case, document, and input-version references;
- a finite task type and allowed state transitions;
- current stage, attempt count, lease owner, and lease expiry;
- prompt, schema, tool, parser, and model versions where applicable;
- output references rather than unbounded payloads;
- retry eligibility and a terminal failure reason; and
- append-only audit events.

Recommended document stages are:

```text
REGISTERED
  -> SECURITY_VALIDATED
  -> PARSED
  -> CLASSIFIED
  -> EXTRACTED
  -> INDEXED
  -> READY_FOR_OFFICER_REVIEW
```

Stages may also enter `RETRY_WAIT`, `FAILED_MANUAL_REVIEW`, or `SUPERSEDED`. A retry resumes from the latest valid checkpoint. Re-uploading changed content creates a new document version and invalidates dependent outputs instead of overwriting history.

## Model-provider abstraction and candidate routing

**CONFIRMED:** Application contracts remain provider- and model-name agnostic even though FPT is the approved inference provider. Agent roles depend on versioned prompts, scoped evidence, authorized tools, structured schemas, and capability requirements.

**PROPOSED and benchmark-gated candidate routing:**

| Task | Primary candidate | Challenger or fallback |
|---|---|---|
| Vietnamese reasoning and structured agent output | Qwen3-30B-A3B instruction/chat endpoint, if verified on FPT | SaoLa3.1-medium 32B; DeepSeek-V4-Flash only if available |
| Key-information extraction | FPT.AI-KIE-v1.7 | deterministic parsers and manual review |
| Complex table extraction | FPT.AI-Table-Parsing-v1.1 | deterministic XLSX parsing and manual review |
| Complex visual document interpretation | Qwen2.5-VL-7B-Instruct | benchmark Qwen3-VL when an approved endpoint is available |
| Embeddings | FPT.AI-e5-large | Vietnamese_Embedding |
| Retrieval refinement | no reranker by default | bge-reranker-v2-m3 only after measured gain |

A catalog name does not prove that the required instruction tuning, structured output, context window, private deployment, retention terms, quota, or regional endpoint is available. Live configuration requires an endpoint capability record and benchmark evidence.

## Model strategy and fine-tuning

The first release does not require fine-tuning. It combines:

- versioned role instructions;
- structured outputs and validation;
- bounded tool calls;
- case-evidence and policy RAG;
- deterministic calculations and state transitions;
- document-family-specific extraction; and
- human confirmation.

Fine-tuning may be considered only when a narrow task has stable ground truth, repeated measured baseline failures, a sufficiently large reviewed dataset, a reproducible before-and-after evaluation, and approved privacy and licensing controls. Current policies and banking rules remain external, versioned, retrievable, and auditable rather than encoded in model weights.

## Retrieval-augmented generation

### Case-evidence retrieval

Case retrieval is always filtered by authorized case and current document version before semantic ranking. Each hit preserves document identifier, version, page, region, passage, extraction method, embedding version, lexical score, vector score, and optional reranking score.

Unconfirmed candidates remain separate from confirmed facts. Retrieval cannot promote content into authoritative state.

### Policy and checklist retrieval

The policy path ingests only approved, versioned sources with owner, effective date, supersession status, and access controls. It returns exact source locations and distinguishes source text from model interpretation.

The path abstains when no authorized corpus exists, no applicable passage is found, or sources conflict. Retrieval failure never means that no policy applies. No official SHB corpus is currently available, so this path remains inactive.

### Retrieval refinement

The default path combines structured filters, PostgreSQL lexical search, and embeddings. A reranker is deployed only if held-out Vietnamese banking evaluations show a material gain in citation precision or evidence recall that justifies additional latency, cost, and provider complexity.

## Security direction

```text
Browser
  -> public HTTPS frontend
  -> authenticated HTTPS API
  -> private service credentials and least-privilege provider calls
```

Required controls include:

- authenticated, case-scoped access and assigned-officer enforcement;
- row-level defense in depth plus backend authorization;
- service identities rather than shared personal credentials;
- secrets stored outside source and frontend bundles;
- encryption in transit and at rest;
- minimal model payloads and redacted logs;
- immutable originals separated from derived data;
- file-type validation, malware scanning, decompression and resource limits;
- egress control and explicit FPT endpoint allow-listing where supported;
- append-only audit events for material reads, writes, transitions, tool calls, and approvals; and
- separate backup and restore validation for database and object storage.

Uploaded documents and retrieved text are untrusted data. They cannot modify permissions, system instructions, tool authorization, workflow state, or approval requirements.

Production banking data remains prohibited until an authorized governance process approves data classification, residency, transfer, retention, deletion, access review, incident response, provider contracts, and security acceptance.

## Failure and recovery

- Duplicate delivery is expected; idempotency prevents duplicate effects.
- A worker crash leaves the queue message recoverable after its lease expires.
- A stale task cannot write to a newer document or case version.
- Schema-invalid model output is rejected and receives bounded retries.
- Unsupported facts or missing source locations are rejected.
- OCR or parser failure creates an unreadable/manual-review state.
- FPT unavailability pauses only affected work and never triggers a hidden public fallback.
- Partial processing cannot create confirmed facts or a ready handoff.
- Retry exhaustion creates `FAILED_MANUAL_REVIEW` with an audit trail.
- Retrieval failure produces abstention, not a negative policy conclusion.
- Restore procedures must be exercised before the environment may be treated as operationally ready.

## Scaling and performance evaluation

The Cloud Run API and worker Job scale independently. The first release runs one queue task per job execution and validates one-at-a-time correctness before enabling parallel executions. Later concurrency is bounded by database connection capacity, queue leases, FPT quotas, document resource requirements, and measured cost. A single case does not start every specialist role concurrently.

Representative tests cover concurrency levels of 1, 4, 8, and 16 documents or bounded model tasks and record:

- API and queue latency;
- time in each processing stage;
- FPT time to first token and completion latency where exposed;
- P50, P95, and P99 end-to-end latency;
- retry, duplicate-delivery, and schema-validation rates;
- document throughput and cost per document or case;
- database, storage, and worker utilization;
- retrieval precision, recall, and citation accuracy; and
- total model calls per case.

Capacity and availability claims require measured evidence. No production-readiness claim follows from deploying the services alone.

## Non-commitments

- **CONFIRMED:** The local prototype is implemented, but no cloud environment is deployed and no production-data authorization exists. Live Supabase, Cloud Run, and FPT verification remain open gates.
- **OPEN QUESTION:** No model or document endpoint has passed the required benchmark.
- **OPEN QUESTION:** No official SHB identity, policy, checklist, retention, region, or production-data authorization has been supplied.
- **OUT OF SCOPE:** Production KYC/AML, CIC, collateral valuation, LOS/ACAS integration, contract execution, and disbursement.
- **OUT OF SCOPE:** Claims of production readiness, regulatory compliance, security certification, or SHB approval.

## Related documents

- [Project Context](PROJECT_CONTEXT.md)
- [Domain Model](DOMAIN_MODEL.md)
- [Agent Architecture](AGENT_ARCHITECTURE.md)
- [Product Boundaries](PRODUCT_BOUNDARIES.md)
- [Open Questions](OPEN_QUESTIONS.md)
- [Decision Log](DECISION_LOG.md)
