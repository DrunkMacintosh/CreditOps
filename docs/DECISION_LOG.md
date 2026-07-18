# Decision Log

## Purpose

This log records confirmed project decisions and their current validity. Proposed technology choices and unresolved SHB-specific rules belong in [Open Questions](OPEN_QUESTIONS.md), not in this log as settled decisions.

| Date | Decision | Reason | Alternatives considered | Status | Conditions that may invalidate or require review |
|---|---|---|---|---|---|
| 2026-07-17 | Use the Credit Case Digital Twin as the central domain object; chat history is not the source of truth. | Material case state must be structured, versioned, and traceable to evidence. | Conversation-centric chatbot; memo-only record | CONFIRMED | Review only if an authorized project source changes the product identity; traceability must remain preserved. |
| 2026-07-17 | Focus the initial phase mainly on stages 2–6: financing need through independent review and human approval. | This provides a bounded SME working-capital preparation and review use case. | Full credit lifecycle; intake-only prototype; post-credit monitoring first | CONFIRMED | Review when an official challenge scope or approved roadmap changes the first-phase boundary. |
| 2026-07-17 | Reserve all material credit decisions, exception dispositions, customer-request approvals, and operational authorizations for humans. | Banking decisions and sensitive actions require accountable human authority. | Autonomous decisioning; AI approval under thresholds | CONFIRMED | This boundary may be strengthened by official policy but must not be weakened without authoritative governance and explicit project direction. |
| 2026-07-17 | Preserve maker–checker separation: underwriting prepares and independent risk review challenges. | Independent challenge reduces self-review and keeps duties distinct. | Single agent prepares and clears; pooled undifferentiated agents | CONFIRMED | Review when the official SHB role and control model is supplied; separation of duties remains required. |
| 2026-07-17 | Require evidence and provenance for every material conclusion and expose unresolved gaps and uncertainty. | The case must be auditable and conclusions must be reviewable. | Uncited narrative; confidence without source lineage | CONFIRMED | May be extended with official retention or audit requirements; the core traceability requirement remains. |
| 2026-07-17 | Use deterministic tools for material calculations, explicit rules, state changes, and controlled actions. | These operations require repeatable behavior, validation, and auditability. | Free-form LLM calculation or action; unvalidated tool calls | CONFIRMED | Tool selection and thresholds may change after requirements are known; deterministic control remains. |
| 2026-07-17 | Use synthetic data only during development and demonstration, with the required visible disclaimer. | No real customer or banking information is needed for the current context and demonstration scope. | De-identified production data; real sandbox data | CONFIRMED | Review only if an authorized governance process explicitly approves another data class and its controls; no such approval currently exists. |
| 2026-07-17 | Keep model-facing application contracts provider agnostic and leave final model selection benchmark-gated. | Model quality and endpoint capabilities must be measured independently from application logic. | Bind agents directly to one provider-specific model contract | CONFIRMED | Revisit model selection when representative benchmark results are recorded; provider abstraction remains required. |
| 2026-07-17 | Treat Evidence Gap Resolution as a workflow capability rather than a separate conversational agent. | Gaps affect shared case state, dependencies, human requests, and re-analysis across roles. | Dedicated gap-chat agent; unstructured task comments | CONFIRMED | Review if a future architecture assigns a distinct service or role; the structured workflow behavior must remain. |
| 2026-07-17 | Use FPT AI Factory in Southeast Asia as the target private AI infrastructure through an hourly H100 VM and local vLLM. | The selected H100 VM supported the initially intended locally hosted, shared-model inference design. | The previously considered infrastructure option | SUPERSEDED by the managed architecture decision below | Retained as history; reconsider only if managed FPT endpoints cannot meet security, availability, capability, or cost requirements. |
| 2026-07-17 | Use Vercel for the frontend, Cloud Run for FastAPI and asynchronous CPU workers, Supabase for durable shared state, queues, private object storage, and pgvector, and FPT AI Factory only for managed inference. | This retains the full Relationship and Intake feature set without operating a GPU, keeps workflow authority outside the model, and provides durable checkpoints and resumable document processing. | FPT H100 VM with vLLM; Vercel Workflows; Supabase Edge Functions as sole backend; Fly.io long-lived workers | CONFIRMED | Review if required data residency, private networking, security controls, provider availability, benchmark quality, latency, or cost cannot be satisfied. |
| 2026-07-18 | Enforce that FPT capability routes activate only from a committed, versioned benchmark-pass record binding capability, model, endpoint, route, prompt, and schema versions (`services/api/src/creditops/infrastructure/fpt/benchmark_records.py`); the registry ships empty, so every route stays DISABLED until a reviewed record is committed, and `FPTCatalog.for_benchmark_evaluation` is the sole documented bypass, reserved for producing that evidence. | Model quality and endpoint capability must be measured, reviewed, and committed as code before any route can affect a live case, never inferred from runtime configuration or environment variables. | Runtime/environment-variable-driven route activation; a soft warning instead of a hard fail-closed gate; letting the evaluation-only path also serve production traffic. | CONFIRMED | Review when a committed benchmark-pass record is added for any capability/model/endpoint/route/prompt/schema combination, or if the evaluation-only bypass is found to leak into a production code path. |
| 2026-07-18 | Establish `shared/synthetic-notice.json` (English and Vietnamese) as the single source of truth for the mandatory synthetic-data notice, asserted by backend and frontend tests against their own pinned constants (`services/api/src/creditops/domain/synthetic_notice.py`, `services/api/tests/unit/domain/test_synthetic_notice.py`, `apps/web/components/shell/synthetic-data-notice.tsx`, `apps/web/tests/lib/synthetic-notice.test.ts`); the mandatory Credit Operations memo disclaimer field is pinned to the canonical Vietnamese notice text followed by a fixed "not a credit decision" guard sentence (`services/api/src/creditops/domain/credit_ops.py`, `SYNTHETIC_DISCLAIMER_VI`). | The non-negotiable synthetic-data label (AGENTS.md) must read identically everywhere it appears and must not silently drift between surfaces or languages. | Independently maintained copy per surface; a shared constant with no cross-surface test enforcement; a disclaimer with no fixed not-a-decision sentence. | CONFIRMED | Review if the wording changes through a reviewed governance decision, or if a new surface renders the notice without asserting against the canonical file. |
| 2026-07-18 | Restrict which Independent Risk Review disposition types may satisfy `G3_RISK_DISPOSITION`: only `NOTED` and `ACCEPTED_RISK` are continue-authorizing; `MAKER_MUST_REVISE` and `ESCALATED` leave the gate OPEN (`services/api/src/creditops/application/orchestration/gates.py`, `G3_CONTINUE_DISPOSITION_TYPES`). | "Đã disposition" (a disposition exists) is not "được tiếp tục" (authorized to continue); a maker-revision or escalation outcome must not silently allow the case to proceed. | Treating any recorded disposition as continue-authorizing; a single boolean "resolved" flag instead of a typed, closed disposition set. | PROPOSED | Review when an official SHB risk-disposition taxonomy is supplied; the fail-closed default (OPEN unless explicitly continue-authorizing) must be preserved regardless of the taxonomy's final labels. |
| 2026-07-18 | Redesign `G2_GAP_REQUEST_APPROVAL` as a pre-Risk `GapRequestBatch` workflow (`services/api/src/creditops/domain/gap_request_batches.py`, `services/api/src/creditops/api/gap_requests.py`, `supabase/migrations/202607180011_gap_request_batches.sql`): a deterministic assembler snapshots every current open evidence gap into a versioned, hashed batch; a human records exactly one disposition against it; an empty batch requires an explicit `NO_OUTBOUND_REQUESTS` disposition rather than silent satisfaction. The prior credit-ops-package-derived G2 path is deleted, removing the Risk-waits-on-Credit-Operations cycle. | G2 governs pre-Risk outbound evidence requests and must not depend on the later Credit Operations package; deriving it from a stale or foreign batch, or leaving an empty batch un-dispositioned, must never satisfy the gate. | Keeping G2 derived from the Credit Operations package; allowing an empty batch to satisfy the gate without an explicit disposition; deriving G2 without binding it to a case-version and open-gap-snapshot hash. | PROPOSED | Review when an official SHB evidence/document-request procedure is supplied; the fail-closed rule (no vacuous satisfaction, staleness on drift) must be preserved. |
| 2026-07-18 | Adopt a transactional outbox plus event-scoped orchestration re-ticks and stranded-task reclaim as the runtime reliability model (`supabase/migrations/202607180007_outbox_events.sql`; kickoff `trigger_ref` in `services/api/src/creditops/application/orchestration/kickoff.py`; `reclaim_stranded` in `services/api/src/creditops/infrastructure/postgres/tasks.py`). A material command commits its domain mutation and one append-only outbox event in the same transaction; a separate dispatcher publishes afterwards, so a crash between commit and publish cannot strand invisible work, and a task success re-ticks the case's orchestration idempotently. | Durable workflow state must survive a crash between a domain commit and its queue publish, and a completed task must not leave the case's orchestration state stale. | Publishing to the queue directly inside the same request with no outbox; relying solely on periodic polling with no event-scoped re-tick; no stranded-task reclaim, leaving abandoned leases unresolved. | CONFIRMED | Review if the dispatcher's best-effort publish semantics prove insufficient, or if outbox event-type scope needs to expand beyond `TASK_READY`. |
| 2026-07-18 | Add a closed, synthetic ten-role set to per-case assignments — `INTAKE_OFFICER, UNDERWRITER, LEGAL_REVIEWER, RISK_REVIEWER, OPS_OFFICER, OPS_CHECKER, ACTION_AUTHORIZER, MONITORING_OFFICER, COLLECTIONS_OFFICER, AUDITOR` (`supabase/migrations/202607180008_case_assignment_roles.sql`), with capabilities derived server-side only from the intersection of the case-role assignment and the actor's JWT role claim (`services/api/src/creditops/api/cases.py`, `_derive_capabilities`; currently exercised for intake mutation capabilities). | A single officer may hold several distinct participant roles on one case, and no client-asserted role may grant a mutation capability without a matching server-recorded assignment. | Keeping the flat one-role-per-case-officer model; deriving capabilities from the JWT claim alone; an open, non-enumerated role vocabulary. | PROPOSED | Review when official SHB roles, RACI, separation-of-duties, and delegation-of-authority rules are supplied; the server-side-assignment-intersected-with-JWT-role fail-closed pattern must be preserved. |
| 2026-07-18 | Compose the worker around a `WORKER_MODE` of `document` or `agent` selecting its queue, with unbenchmarked inference left DISABLED and each specialist task type failing closed to `ManualReviewProcessor` rather than a fabricated result (`services/api/src/creditops/worker/main.py`, `build_runtime`; `services/api/src/creditops/config.py`). | The worker must refuse to run without injected real dependencies and must never substitute a synthetic or partial result when inference or storage is unavailable for a given task type. | A single undifferentiated worker mode; falling back to a degraded automated result when a capability is unavailable; crashing instead of routing to manual review. | CONFIRMED | Review if additional worker modes or task types are introduced; the fail-closed-to-manual-review default must be preserved. |

## Superseded infrastructure decision detail — 2026-07-17

**Decision — SUPERSEDED:** Use an hourly FPT AI Factory H100 VM as the target private AI infrastructure.

**Selected configuration:**

- 1 × NVIDIA H100 SXM5;
- 80 GB HBM3;
- 192 GB system RAM;
- 16 CPU cores;
- Intel Xeon Platinum Processor 8462Y+;
- 3 TB local NVMe;
- Southeast Asia region; and
- hourly rental.

**Reason:**

- suitable for locally hosted open-weight LLM inference;
- supports keeping data and inference in a controlled environment;
- supports one shared model for all logical agent roles;
- provides capacity for a strong model plus KV cache and runtime overhead;
- supports benchmarking and a future horizontal-replica scaling direction; and
- aligns with the desired private-bank infrastructure concept.

**Superseded history:** The previously considered infrastructure option is retired because FPT AI Factory now provides the confirmed provider, region, and VM target. This history is retained without carrying the obsolete option into the current architecture.

**Conditions that may invalidate or require review:**

- required CUDA or container support is unavailable;
- network restrictions prevent internal API serving;
- local storage is unsuitable and no persistent alternative exists;
- the selected model cannot meet Vietnamese banking benchmarks;
- continuous-operation cost exceeds the project budget; or
- competition rules require another infrastructure provider.

No VM was provisioned. The managed architecture below supersedes this configuration before implementation. The provider-neutral application-contract principle remains valid.

## Managed architecture decision detail — 2026-07-17

**Decision — CONFIRMED:** Use the following service boundaries:

- Vercel hosts the Vietnamese Next.js frontend and does not orchestrate document processing;
- Cloud Run hosts the FastAPI API, deterministic workflow authority, provider gateway, and an asynchronous `creditops-worker` Job;
- Supabase Postgres stores the Credit Case Digital Twin, EvidenceGraph, workflow state, checkpoints, retrieval metadata, permissions metadata, and audit records;
- Supabase Queues carries durable work identifiers with bounded leases, retries, and archival;
- private Supabase Storage stores immutable originals and versioned derived artifacts, with a separate backup and restore design;
- Supabase pgvector supports case-evidence and approved-policy retrieval indexes;
- browser uploads use a backend-created upload intent, with a short-lived signed upload for normal uploads or authenticated resumable upload under Storage RLS when required, and do not pass document bodies through Vercel Functions; and
- FPT AI Factory provides managed reasoning, document, vision, embedding, and optional reranking inference only.

Cloud Run, not Supabase or FPT, owns orchestration and deterministic banking logic. Supabase is the durable source of shared state. FPT model output remains non-authoritative until schema validation and the applicable human confirmation gate.

**Candidate model direction — PROPOSED and benchmark-gated:** Qwen3-30B-A3B instruction/chat as the main reasoning candidate if an appropriate FPT endpoint is available; SaoLa3.1-medium 32B as the Vietnamese challenger; FPT.AI-KIE-v1.7 and FPT.AI-Table-Parsing-v1.1 for document extraction; Qwen2.5-VL-7B-Instruct as the complex-visual fallback; FPT.AI-e5-large compared with Vietnamese_Embedding for retrieval; and bge-reranker-v2-m3 only if measured retrieval gains justify a managed endpoint. DeepSeek-V4-Flash remains a challenger only if FPT exposes a suitable endpoint.

## Current non-decisions

The following are intentionally not decided:

- exact framework versions and whether an additional workflow library is necessary beyond the explicit state machine;
- final model, embedding model, reranker, KIE, table, or document-vision endpoint after benchmarking;
- provider regions, private networking, backup and restore settings, quotas, scaling, and monitoring configuration;
- official SHB policies, rules, checklist, roles, gates, or memo format;
- production integrations; and
- evaluation metrics and pass thresholds.

## Change discipline

Future entries should include date, decision, reason, alternatives considered, status, and invalidation conditions. If a decision is superseded, retain the original row and add a new row that references it rather than rewriting history.

## Related documents

- [Project Context](PROJECT_CONTEXT.md)
- [Product Boundaries](PRODUCT_BOUNDARIES.md)
- [Technical Direction](TECHNICAL_DIRECTION.md)
- [Open Questions](OPEN_QUESTIONS.md)
