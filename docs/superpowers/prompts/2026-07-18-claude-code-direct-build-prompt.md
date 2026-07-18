# Prompt trực tiếp cho Claude Code — SHB CreditOps EvidenceGraph

Sao chép toàn bộ nội dung từ phần `BEGIN PROMPT` đến `END PROMPT` vào Claude Code tại repository `CreditOpt`.

---

## BEGIN PROMPT

Bạn đang làm việc trực tiếp trong repository:

```text
/Users/an/Desktop/AIV/CreditOpt
```

Mục tiêu của bạn là triển khai hệ thống SHB CreditOps EvidenceGraph theo master design đã được duyệt. Không viết thêm implementation plan, proposal hoặc design document. Hãy kiểm tra repository rồi bắt đầu sửa code, migration, API, worker, frontend và test theo các wave đã có trong spec.

### 1. Nguồn sự thật bắt buộc phải đọc toàn bộ

Trước khi thay đổi file, đọc đầy đủ theo thứ tự:

1. `/Users/an/Desktop/AIV/CreditOpt/AGENTS.md`
2. `/Users/an/Desktop/AIV/CreditOpt/docs/superpowers/specs/2026-07-18-full-credit-lifecycle-agent-workflow-design.md`
3. `/Users/an/Desktop/AIV/CreditOpt/docs/OPEN_QUESTIONS.md`
4. `/Users/an/Desktop/AIV/CreditOpt/docs/DECISION_LOG.md`
5. `/Users/an/Desktop/AIV/CreditOpt/docs/PROJECT_CONTEXT.md`
6. `/Users/an/Desktop/AIV/CreditOpt/docs/AGENT_ARCHITECTURE.md`
7. `/Users/an/Desktop/AIV/CreditOpt/docs/TECHNICAL_DIRECTION.md`
8. Các migrations, domain models, application services, ports, infrastructure adapters, API routes, prompts, tests và frontend hiện có liên quan đến wave đang thực hiện.

Master design ở mục 2 là contract kiến trúc chính. Đọc toàn bộ file, không chỉ heading hoặc summary. Nếu context bị compact trong quá trình làm việc, đọc lại spec và trạng thái git trước khi tiếp tục.

Reference revision của master design:

```text
057a1c0 docs: design full credit lifecycle agent workflow
```

Repository có thể đã thay đổi sau revision này. Luôn kiểm tra `git status`, `git log`, current branch và diff trước khi sửa. Bảo toàn mọi thay đổi hiện có của người dùng. Không dùng `git reset --hard`, `git checkout --`, xóa file hoặc sửa ngoài scope.

Tại thời điểm prompt được tạo, các path sau là untracked user content và không được tự ý stage, sửa hoặc xóa:

```text
.superpowers/
evaluation/
services/api/tests/evaluation/
```

### 2. Chế độ thực thi

- Không tạo implementation-plan file.
- Không dừng sau khi tóm tắt kế hoạch.
- Sau khi đọc context, báo ngắn gọn repository facts và wave đang bắt đầu, rồi triển khai ngay.
- Dùng delivery decomposition ở Section 22 của master design làm thứ tự thực thi có sẵn.
- Làm từng wave thành những thay đổi nhỏ, có test và commit độc lập.
- Sau mỗi wave: chạy targeted tests, full relevant tests, static checks và build; đọc output; sửa lỗi; chỉ commit khi verification thực sự pass.
- Sau khi commit một wave, tiếp tục wave kế tiếp nếu không có blocker thuộc nhóm phải dừng.
- Không đánh dấu feature hoàn tất nếu mới có schema, UI mock hoặc test stub.
- Không dùng placeholder, fake success response, hardcoded business result hoặc route giả để làm sản phẩm trông hoàn chỉnh.
- Không bỏ test bằng `skip`, không nới assertion để che bug và không dùng hidden fallback.

Bạn có thể dùng parallel subagents cho các subtask độc lập như backend contracts, frontend surfaces và test audit. Không cho nhiều agent sửa cùng file hoặc cùng migration. Lead agent phải đọc diff, hợp nhất và tự chạy verification; không tin completion report của subagent nếu chưa kiểm tra.

### 3. Product identity và architecture không được thay đổi

Đây là hệ thống multi-agent có khả năng kiểm chứng để hỗ trợ chuẩn bị và rà soát hồ sơ cấp tín dụng vốn lưu động SME/KHDN.

Đối tượng trung tâm là structured, versioned, traceable `Credit Case Digital Twin`. Chat history không phải source of truth.

Target architecture:

```text
Vietnamese Next.js frontend on Vercel
  -> Cloud Run FastAPI API
  -> Supabase Postgres + RLS + pgvector
  -> Supabase Queues + private Storage
  -> Cloud Run worker/job entrypoints
  -> provider-neutral model gateway
  -> FPT AI Factory managed inference only
```

Ownership:

- Supabase giữ durable shared state, checkpoints, object references, queues, retrieval metadata, audit và EvidenceGraph.
- Cloud Run sở hữu orchestration, authorization, deterministic banking logic, tools và workers.
- FPT chỉ inference; không phải agent, workflow engine, approval service, tool executor hoặc source of truth.
- Vercel/frontend chỉ hiển thị canonical state và gửi user intent qua backend.
- Browser không gọi FPT trực tiếp và không nhận Supabase service-role key.
- Document upload trực tiếp vào private Supabase Storage chỉ bằng backend-created short-lived intent.

### 4. Data và claim boundary

Chỉ sử dụng synthetic data trong development, test và demonstration. Không yêu cầu, nhập, tạo hoặc xử lý dữ liệu ngân hàng/khách hàng thật.

Mọi workspace và artifact export phải hiển thị đúng canonical notice:

> All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.

Không gọi policy synthetic là policy SHB. Không claim:

- production readiness;
- regulatory compliance;
- security certification;
- official SHB policy;
- SHB approval hoặc endorsement;
- live banking-system integration nếu chưa có verification thật.

Tài liệu quy trình 14 giai đoạn là context tín dụng KHDN tổng quát, không có mã văn bản, version, effective date hoặc xác nhận là quy trình SHB. Không hard-code checklist, SLA, threshold, risk appetite, delegation of authority, condition, waiver hoặc policy rule chưa có nguồn chính thức.

Mọi statement material phải được phân loại là `CONFIRMED`, `PROPOSED`, `ASSUMPTION`, `OPEN QUESTION` hoặc `OUT OF SCOPE`. Material rule thiếu nguồn phải dùng versioned synthetic configuration có nhãn hoặc fail closed.

### 5. Ranh giới quyền lực tuyệt đối

Không agent nào được:

- phê duyệt hoặc từ chối tín dụng;
- waive policy, exception hoặc condition;
- đưa legal determination cuối cùng;
- ký hợp đồng;
- gửi giao tiếp khách hàng nếu thiếu human approval;
- giải ngân, dừng hạn mức, cơ cấu nợ, thu tiền, giải chấp, xử lý tài sản, gọi bảo lãnh, khởi kiện hoặc write-off;
- mutate sensitive banking system;
- tự xác nhận candidate fact hoặc tự đóng gap/conflict/challenge;
- tự mở rộng goal hoặc tool permission.

Underwriting là maker. Independent Risk Review là checker. Con người có thẩm quyền quyết định.

Mọi action nhạy cảm chỉ là `ProposedAction` cho tới khi backend xác minh deterministic preconditions, actor capability, exact case/artifact version, explicit human authorization, idempotency key và audit record. Trong phạm vi hiện tại chỉ dùng mock adapter có nhãn; không real fund movement hoặc external mutation.

### 6. Tám logical agents đã duyệt

Giữ đúng tám role application-level sau; không tự tách hoặc gộp lại khi chưa có quyết định mới:

1. Case Orchestrator
2. Relationship & Intake
3. Credit Underwriting
4. Legal, Compliance & Collateral
5. Independent Risk Review
6. Credit Operations
7. Post-Credit Monitoring
8. Collections & Recovery

Evidence Gap Resolution là shared workflow capability, không phải agent thứ chín.

Các role không bắt buộc dùng model hoặc service riêng. Specialization đến từ goal, instruction, tool allowlist, schema, evidence scope, permission và human gate.

Agent không truyền authoritative state bằng free-form chat. Chúng tương tác qua durable task, exact case version, persisted `ContextManifest`, versioned artifact, structured claims, immutable handoff, domain event, provenance và append-only audit.

### 7. Workflow 14 giai đoạn

Phải giữ đầy đủ lifecycle trong master design:

1. Tìm kiếm và tiếp cận khách hàng.
2. Tiếp nhận và xác định nhu cầu tín dụng.
3. Thu thập và kiểm tra hồ sơ.
4. Thẩm định khách hàng và đề nghị cấp tín dụng.
5. Lập và trình phương án cấp tín dụng.
6. Thẩm định độc lập và human credit decision.
7. Thông báo tín dụng.
8. Đàm phán và ký hồ sơ.
9. Hoàn thiện biện pháp bảo đảm.
10. Kiểm tra điều kiện giải ngân.
11. Proposed disbursement và mock execution có human authorization.
12. Quản lý và giám sát sau cấp tín dụng.
13. Repayment ledger, thu nợ và collections support.
14. Tất toán hoặc recovery preparation.

Giai đoạn 7–14 chỉ được bật khi typed contracts, permissions, human gates và synthetic/mock adapters tương ứng đã có. Không làm route có vẻ hoạt động bằng hardcoded business data.

### 8. Shared memory và reasoning inheritance

Triển khai typed shared memory theo bảy lớp trong spec:

1. Procedural memory.
2. Authoritative case memory.
3. Evidence memory.
4. Analytical memory.
5. Workflow memory.
6. Longitudinal memory.
7. Policy memory tách khỏi customer evidence.

Không tạo generic JSON memory table làm authoritative source. Dùng typed domain records, versioning, provenance và invalidation.

Mỗi model execution phải bind immutable/versioned `GoalContract` và persisted `ContextManifest` gồm exact case version, authorization snapshot, prompt/schema/model/tool versions, facts, human decisions, upstream handoffs, gaps, conflicts, challenges, retrieval/tool refs, explicit exclusions, budgets và stable `contextHash`.

Không lưu hoặc hiển thị hidden chain-of-thought, scratchpad hoặc raw reasoning tokens. Nếu provider trả reasoning field, gateway loại bỏ trước khi persist.

Kế thừa reasoning qua structured claims và derivation graph:

```text
document region
  -> confirmed fact
  -> deterministic calculation
  -> underwriting/legal finding
  -> risk challenge
  -> maker response
  -> operations package
  -> human decision
  -> monitoring observation
  -> collection/recovery artifact
```

Downstream role phải disposition inherited claim: independently verified, accepted conditionally, challenged, rejected, superseded, new claim hoặc unresolved. Evidence mới làm affected downstream artifact `STALE`; không overwrite hoặc xóa lịch sử.

### 9. Graph-guided hybrid RAG

Không dùng full community/global GraphRAG làm default cho case workflow.

Sử dụng typed Postgres EvidenceGraph + pgvector:

```text
authorized task/query
  -> tenant/case/version/effective-date filters
  -> typed seed nodes
  -> bounded graph traversal
  -> candidate source-region refs
  -> lexical + vector retrieval trên original passages
  -> optional rerank nếu benchmark chứng minh gain
  -> hydrate immutable original source
  -> token packing
  -> FPT inference
  -> citation validation
```

Tách Case Evidence Graph, Policy Graph và Analytical Reasoning Graph. Graph không thay tài liệu gốc. Mọi material citation phải resolve tới document/policy ID, version, page/sheet/region, passage hash và authorization scope. Cấm cross-case retrieval. Candidate fact không trở thành confirmed fact do retrieval.

Nếu không có authorized/effective policy corpus, trả `POLICY_SOURCE_UNAVAILABLE` hoặc abstention; không dùng model memory làm policy.

### 10. Deterministic runtime contracts

Material calculation, reconciliation, rule, state transition, authorization và controlled action phải do deterministic code thực hiện.

Queue message chỉ chứa opaque identifiers và contract version. Dependency, evidence, permission, lease, attempt và context phải tải từ durable state sau khi worker claim task.

Mọi material command commit atomically:

1. domain mutation;
2. versioned artifact/lineage;
3. audit record;
4. outbox event.

Consumer dùng inbox dedup. Duplicate delivery không tạo duplicate effect. Ack/archive chỉ sau durable success.

Bốn worker modes đã duyệt:

- Document Worker
- Agent Worker
- Event Worker, bao gồm outbox và recovery sweep
- Action Worker cho authorized mock actions

Có thể dùng cùng image nhưng phải tách entrypoint, queue, service account, timeout, concurrency và permission.

External-effect timeout tạo `EXECUTION_UNKNOWN`; không blind retry. Human reconciliation xác định receipt trước thao tác tiếp theo.

### 11. Những P0 phải xử lý trước

Đừng mở rộng bề mặt sản phẩm trước khi khép kín các blocker này:

1. Wire runnable worker composition root thay cho `worker/main.py` refuse-to-run.
2. Implement concrete document-ingestion persistence processor end-to-end.
3. Hoàn thiện FastAPI document review, confirmation, evidence, conflict, gap, handoff và audit contracts.
4. Implement assigned-intake completion → immutable IntakeHandoff → G1.
5. Sửa G2 circular dependency dứt điểm.
6. Tự phát idempotent orchestration tick sau task/gate/handoff/evidence event.
7. Khép kín queue → Cloud Run dispatch, polling, lease và recovery.
8. Implement `MAKER_MUST_REVISE` feedback branch và Risk rerun.
9. Implement multi-role assignments, capabilities, maker–checker enforcement và `HumanCreditDecision`.
10. Chuẩn hóa canonical synthetic notice giữa frontend, memo và domain schema.
11. Enforce benchmark-pass record trước khi FPT capability route được `ACTIVE`.
12. Implement `GoalContract`, `ContextManifest`, structured reasoning inheritance, transactional outbox và inbox dedup.

#### Sửa G2 bắt buộc

Không derive G2 từ `credit_ops_package`.

Sau specialist assessments, deterministic gap assembler tạo versioned `GapRequestBatch` từ current open-gap snapshot/hash. Human disposition phải là một trong:

- `APPROVED_ALL`
- `APPROVED_WITH_CHANGES`
- `REJECTED`
- `NO_OUTBOUND_REQUESTS`

Không có request vẫn cần explicit `NO_OUTBOUND_REQUESTS`; không silent satisfaction. Gate chỉ satisfied khi batch/disposition bind current case version, mọi item được disposition và hash còn khớp current gap snapshot. Evidence/gap/case version thay đổi làm batch cũ stale và mở gate mới.

Xóa đường derive G2 hiện tại dựa trên `credit_ops_package`/`_maybe_satisfy_g2()`, có migration/backfill policy rõ và regression test chứng minh Risk không còn chờ Credit Operations trong vòng tròn.

### 12. Thứ tự thực thi đã có sẵn

Không viết lại plan. Thực hiện trực tiếp theo các wave trong Section 22 của spec:

- Wave 0: truth alignment và safety contracts.
- Wave 1: runtime foundation.
- Wave 2: giai đoạn 1–3.
- Wave 3: giai đoạn 4–6.
- Wave 4: giai đoạn 7–10.
- Wave 5: giai đoạn 11.
- Wave 6: giai đoạn 12.
- Wave 7: giai đoạn 13–14.
- Wave 8: hardening, evaluation và deployment gates.

Bắt đầu bằng Wave 0/P0 trên current repo. Không làm Wave sau nếu acceptance quan trọng của Wave trước chưa pass. Nếu một phần Wave bị chặn bởi official SHB source, real integration, cloud credential hoặc governance, fail closed phần đó, ghi blocker chính xác và tiếp tục phần synthetic/local độc lập vẫn làm được.

### 13. Backend implementation discipline

Giữ module boundaries hiện có:

- `domain/`: entities, value objects, invariants, transitions.
- `application/`: use cases, orchestration, context builder, deterministic services.
- `application/ports/`: provider-neutral repository/queue/storage/model/action contracts.
- `infrastructure/postgres/`, `supabase/`, `fpt/`, `mock/`: adapters.
- `api/`: auth, validation, command/query và error mapping; không chứa banking logic.
- `worker/`: composition roots; không chứa domain logic.
- `prompts/`: versioned role prompts với untrusted-document boundary.

Follow existing conventions. Không thực hiện unrelated refactor. Migration append-only, không sửa migration đã áp dụng. Mọi schema/material payload có version. Mọi mutation material dùng expected version/ETag và idempotency khi phù hợp.

Only `Assigned Intake Officer` được mutate financing request, candidate dispositions, intake conflicts/gaps, outbound-request workflow và IntakeHandoff trong prototype hiện tại. Assignment/delegation là audited server command, không suy từ frontend.

Credit Operations Maker, Credit Operations Checker và Action Authorizer phải tách bằng capability, actor và artifact version. Nếu official role mapping chưa có, synthetic role config phải gắn nhãn và mọi unknown authority fail closed.

Error response contract:

```json
{
  "code": "STABLE_MACHINE_CODE",
  "messageVi": "Thông báo an toàn cho người dùng",
  "retryable": false,
  "correlationId": "opaque-id",
  "details": {}
}
```

Không trả stack trace, prompt, secret hoặc raw provider response cho browser.

### 14. Frontend implementation discipline

Giao diện Vietnamese-first. Người dùng làm việc trên task, evidence, artifact, gap, challenge và human gate; chat chỉ giải thích, không lưu authoritative state.

Default entry là `/cong-viec`, lấy work items theo server capabilities và assignments. Query/route không cấp quyền.

Case workspace phải có summary, evidence/source viewer, fact ledger, gaps/conflicts, agent activity, structured lineage, versions/stale state, human gates và audit.

Không dùng generic `Phê duyệt` cho mọi action. Label phải nói đúng hiệu lực như:

- `Duyệt nội dung yêu cầu bổ sung`
- `Ghi nhận disposition cho challenge`
- `Ghi nhận quyết định của cấp có thẩm quyền`
- `Uỷ quyền hành động đề xuất`

Human gate không preselect approval, không optimistic-complete và phải hiển thị exact case/artifact version, evidence, unresolved items, authority, rationale và consequence.

UI state bắt buộc:

- `202`: queued/running, bounded polling, manual refresh.
- `401`: session expired; draft chỉ giữ trong tab memory.
- `403`: không render mutation control.
- `404`: không phân biệt missing với unassigned.
- `409`: giữ draft, cấm auto-resubmit, reload/review version mới.
- `429`: tôn trọng `Retry-After`.
- retryable `5xx`: retry theo section.
- unknown enum/schema: fail closed.
- `RETRY_WAIT`, `FAILED_MANUAL_REVIEW`, `STALE`, `SUPERSEDED`, `CONTRACT_PENDING`: trạng thái và action riêng.

Không lưu token, signed URL, secret, document text hoặc privileged data trong URL, localStorage, sessionStorage, analytics hoặc log.

### 15. FPT và model routing

Giữ provider-neutral capability gateway. Candidate hiện tại:

- reasoning: DeepSeek-V4-Flash;
- document vision: Qwen2.5-VL-72B-Instruct;
- embedding: multilingual-e5-large;
- optional reranker: bge-reranker-v2-m3;
- managed KIE/table endpoint cần được xác nhận.

Decision log còn giữ Qwen3-30B-A3B, SaoLa3.1-medium 32B, FPT KIE/Table, Qwen2.5-VL-7B, FPT.AI-e5-large và Vietnamese_Embedding làm challenger. Không chọn bằng cảm tính hoặc chỉ vì model name có trong catalog.

Capability route chỉ `ACTIVE` khi có endpoint allowlist, credentials/config hợp lệ, benchmark-pass record gắn endpoint/model/prompt/schema version, Vietnamese quality, structured output, citation, safety, latency/quota/cost và data-control gates. Thiếu record thì `DISABLED`/manual review/abstention.

Không hidden non-FPT fallback.

Fine-tuning không nằm trên critical path. Chỉ mở MLE wave riêng sau khi baseline + RAG + deterministic tools + prompt/schema vẫn không đạt approved threshold trên representative holdout. Nếu cần, ưu tiên FPT managed fine-tuning/endpoint; không quay lại self-hosted GPU. Không train trên customer documents tùy tiện.

### 16. Test-driven delivery

Viết failing test cho behavior/invariant trước hoặc cùng thay đổi; xác nhận test fail đúng lý do, implement minimal correct behavior, chạy lại targeted test, rồi refactor an toàn.

Phải có:

- domain invariant và exact-decimal tests;
- API/schema/contract tests;
- RLS/cross-case isolation tests;
- worker lease/checkpoint/duplicate/crash/stale-write tests;
- outbox/inbox/idempotency tests;
- GraphRAG scope, original-source hydration và citation tests;
- prompt-injection/untrusted-document tests;
- role/assignment/maker–checker/human-gate negative tests;
- frontend component/error/stale/accessibility tests;
- BFF Origin/CSRF/cookie/path/method/secret tests;
- E2E synthetic workflows;
- failure injection cho FPT timeout, malformed output, queue redelivery, DB conflict và `EXECUTION_UNKNOWN`;
- live FPT synthetic smoke/evaluation khi endpoint được cung cấp.

Test tài liệu theo kiểu document-by-document và case-by-case. Không đưa nhiều document vào một test khi mục tiêu là xác minh extraction/provenance của từng tài liệu.

Acceptance tối thiểu:

- zero cross-case leakage;
- zero unauthorized mutation;
- zero human-gate bypass;
- zero stale write;
- candidate không tự thành confirmed fact;
- every material claim có evidence/tool lineage hoặc explicit uncertainty/gap;
- maker–checker enforced bằng actor, capability và artifact version;
- no agent credit decision/waiver/sign/disbursement/restructure/recovery execution;
- no blind external-effect retry;
- no hidden fallback;
- no stale source retrieval;
- no stored chain-of-thought;
- every material mutation có audit/outbox/idempotency;
- canonical synthetic notice hiển thị đúng;
- backend tests, frontend tests, typecheck, lint và production build pass.

Không claim FPT inference end-to-end nếu chưa có live managed FPT synthetic smoke ghi rõ endpoint/model/prompt/schema versions.

### 17. Git và verification

- Trước mỗi wave, kiểm tra current diff và giữ user changes.
- Stage exact files; không dùng `git add .` nếu có unrelated changes.
- Dùng conventional commits có scope rõ.
- Không amend/rewrite user commits.
- Không push, mở PR, deploy, provision cloud hoặc tạo secret trừ khi user đưa chỉ dẫn riêng, rõ ràng trong phiên Claude hiện tại.
- Trước mọi completion claim, chạy fresh verification command và đọc full output.
- Báo chính xác test nào đã chạy, pass/fail/skipped và lý do.
- Passing local tests không chứng minh cloud deployed hoặc production-ready.

### 18. Khi nào được dừng và hỏi người dùng

Chỉ dừng khi gặp một trong các blocker sau:

- cần official SHB rule, threshold, checklist, RACI, authority hoặc legal interpretation chưa có;
- cần real data hoặc production integration authorization;
- cần cloud/FPT credentials, secret, endpoint hoặc external state change chưa được cấp;
- cần destructive action hoặc thay đổi ngoài scope;
- hai authoritative sources xung đột và lựa chọn sẽ thay đổi material banking behavior;
- cùng một blocker kỹ thuật đã được điều tra đầy đủ và không còn safe local work độc lập.

Khi dừng, cung cấp:

1. bằng chứng cụ thể;
2. ảnh hưởng đến wave/acceptance;
3. những phần vẫn hoàn thành được;
4. một câu hỏi ngắn, duy nhất cần người dùng trả lời.

Không dùng missing official rule làm lý do dừng toàn project nếu vẫn có thể triển khai safe typed contract, fail-closed behavior, synthetic configuration hoặc independent test.

### 19. Bắt đầu ngay

Thực hiện ngay các bước sau, không tạo plan document:

1. Đọc đầy đủ nguồn ở Section 1.
2. Kiểm tra git status/log và inventory code/tests/migrations hiện tại.
3. Xác minh lại P0 facts trên current revision, không dựa mù vào spec.
4. Bắt đầu Wave 0 bằng các thay đổi nhỏ nhất cần thiết để thống nhất truth/safety contracts và tạo failing regression tests cho G2, canonical notice, benchmark activation và authority boundaries.
5. Tiếp tục qua các wave theo Section 12, test và commit mỗi checkpoint.

Trong response đầu tiên, chỉ báo ngắn:

- current branch/HEAD;
- user changes sẽ được bảo toàn;
- P0 đầu tiên bạn xác minh;
- test đầu tiên bạn sẽ chạy hoặc viết;
- sau đó bắt đầu dùng tools và sửa code ngay.

## END PROMPT
