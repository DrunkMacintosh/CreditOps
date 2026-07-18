# Thiết kế workflow đa agent cho toàn bộ vòng đời cấp tín dụng KHDN

**Ngày:** 2026-07-18

**Trạng thái:** Thiết kế đã được người dùng duyệt về hướng kiến trúc; chờ người dùng duyệt bản đặc tả viết này trước khi lập implementation plan.

**Phạm vi:** Master design cho 14 giai đoạn của vòng đời cấp tín dụng khách hàng doanh nghiệp, từ tiếp cận khách hàng đến tất toán hoặc xử lý nợ.

**Repository được đối chiếu:** `CreditOpt`, commit `dd06295` trên nhánh `agent/agents-console`.

**Nguồn quy trình do người dùng cung cấp:** `/Users/an/.codex/attachments/35fa95a8-5916-41a7-a827-7130db062635/pasted-text.txt`, SHA-256 `bacc1f6e3ad58adaadc06df63d8c5c6167d0d5acb7d08e882686ba44088f635e`.

## 1. Mục đích của tài liệu

Tài liệu này là nguồn context thiết kế thống nhất cho người triển khai và Claude Code. Nó hợp nhất:

- toàn bộ quy trình cấp tín dụng KHDN 14 giai đoạn do người dùng cung cấp;
- ranh giới quyền hạn giữa con người, agent, deterministic engine và hệ thống ngoài;
- kiến trúc backend, frontend, shared memory, reasoning inheritance và Graph-guided RAG;
- trạng thái code hiện có và các blocker phải xử lý trước;
- mô hình dữ liệu, API, event, task, checkpoint, audit và human gate;
- yêu cầu bảo mật, khả năng kiểm chứng, hiệu quả token, test và vận hành;
- các điều chưa có nguồn chính thức và không được tự suy đoán thành rule ngân hàng.

Đây là **design specification**, không phải implementation plan, không phải tài liệu chính sách tín dụng, không phải ý kiến pháp lý và không phải bằng chứng hệ thống đã production-ready.

## 2. Từ khóa phân loại bắt buộc

Mọi quyết định trong quá trình build phải sử dụng các nhãn sau:

- **CONFIRMED:** đã được nguồn có thẩm quyền trong project hoặc quyết định hiện hành xác nhận.
- **PROPOSED:** thiết kế được chấp thuận để triển khai thử nghiệm nhưng chưa phải chính sách SHB.
- **ASSUMPTION:** giả định cần thiết để thiết kế; không được biến thành rule ngân hàng.
- **OPEN QUESTION:** thiếu nguồn hoặc có xung đột; phải fail closed đối với hành động nhạy cảm.
- **OUT OF SCOPE:** không được triển khai như hành vi thật trong phạm vi hiện tại.
- **SUPERSEDED:** quyết định lịch sử đã được thay thế, chỉ giữ để truy vết.

Thứ tự ưu tiên nguồn:

1. Chỉ dẫn hiện tại, rõ ràng của người dùng trong phạm vi an toàn và quản trị project.
2. Tài liệu challenge hoặc tài liệu SHB chính thức trong repository.
3. Tài liệu quy trình được project team xác nhận.
4. `AGENTS.md`.
5. Tài liệu trong `docs/`.
6. `docs/DECISION_LOG.md`.
7. Giả định được gắn nhãn.

Khi hai nguồn xung đột, phải giữ cả hai diễn giải, ghi nguồn, đánh giá tác động, đưa vào `OPEN_QUESTIONS.md` và không tự implement material banking rule.

## 3. Sự thật nền tảng và ranh giới không thương lượng

### 3.1 Product identity

**CONFIRMED:** Sản phẩm là hệ thống multi-agent có khả năng kiểm chứng để hỗ trợ chuẩn bị và rà soát hồ sơ cấp tín dụng vốn lưu động cho SME/KHDN. Đối tượng trung tâm là **Credit Case Digital Twin** có cấu trúc, version và provenance; chatbot hoặc lịch sử hội thoại không phải source of truth.

**CONFIRMED:** Supabase là nguồn durable state và checkpoint. Cloud Run sở hữu orchestration, authorization và deterministic business logic. FPT AI Factory chỉ cung cấp model inference. Vercel chỉ hiển thị giao diện và gửi hành động người dùng qua backend.

**CONFIRMED:** Agent không có quyền phê duyệt hoặc từ chối tín dụng. Agent chỉ thu thập, trích xuất, phân tích, challenge, chuẩn bị artifact và đề xuất hành động trong phạm vi schema/permission.

### 3.2 Human-only authority

AI không bao giờ được:

- phê duyệt hoặc từ chối cấp tín dụng;
- miễn, hạ cấp hoặc bỏ qua policy/condition;
- đưa ra legal determination cuối cùng;
- ký hợp đồng hoặc xác nhận thẩm quyền ký thay con người;
- gửi giao tiếp khách hàng nếu chưa có human approval phù hợp;
- giải ngân, dừng hạn mức, cơ cấu nợ, giải chấp, xử lý tài sản bảo đảm, khởi kiện hoặc xóa nợ;
- tự mutate hệ thống tác nghiệp nhạy cảm;
- tự tạo dữ kiện còn thiếu hoặc coi model output là confirmed fact;
- tự xóa gap, conflict, challenge hoặc disagreement khỏi lịch sử.

Mọi hành động nhạy cảm phải có deterministic validation, exact artifact version, explicit human authorization, idempotency key và audit trail.

### 3.3 Maker–checker và provenance

- Underwriting là maker; Independent Risk Review là checker.
- Hai vai trò không được dùng cùng execution hoặc cùng actor để vừa tạo vừa độc lập clear một kết luận.
- Mọi kết luận material phải dẫn về evidence region, confirmed fact, deterministic calculation hoặc policy passage có version.
- Confidence không thay thế evidence.
- Uncertainty, assumptions, unresolved gaps và disagreements phải hiển thị tới người quyết định.
- Material arithmetic, rule evaluation, state transition và controlled action do code deterministic thực hiện, không giao cho LLM.

### 3.4 Data boundary

**CONFIRMED theo project governance hiện tại:** Chỉ dùng dữ liệu synthetic trong phát triển và demonstration. Phải hiển thị đúng thông báo:

> All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.

Không được mô tả policy synthetic là policy SHB. Không được dùng dữ liệu ngân hàng hoặc dữ liệu cá nhân thật cho đến khi có văn bản cho phép, data classification, lawful basis, residency, retention, DLP, access control, incident response và production-data governance được phê duyệt.

## 4. Thẩm quyền của nguồn quy trình 14 giai đoạn

Tài liệu người dùng cung cấp mô tả quy trình cấp tín dụng KHDN ở mức tổng quát và dùng các cụm như “thường”, “có thể”, “tùy theo mô hình tổ chức của từng ngân hàng”. Tài liệu không có tên SHB, mã quy trình, số hiệu, version, ngày hiệu lực hoặc cấp ban hành.

Do đó:

- **CONFIRMED:** đây là business-process context do người dùng cung cấp để thiết kế sản phẩm;
- **NOT CONFIRMED AS SHB POLICY:** không được gọi đây là quy trình nội bộ chính thức của SHB;
- **OPEN QUESTION:** checklist, RACI, SLA, authority matrix, case states, materiality thresholds và policy corpus chính thức chưa có;
- **IMPLEMENTATION RULE:** workflow phải cấu hình được, versioned và fail closed ở mọi material gate chưa có source chính thức.

## 5. Toàn bộ quy trình cấp tín dụng KHDN làm context nghiệp vụ

### Giai đoạn 1 — Tìm kiếm và tiếp cận khách hàng

Chuyên viên quan hệ khách hàng doanh nghiệp tìm kiếm, tiếp cận và nhận diện doanh nghiệp có nhu cầu dùng sản phẩm tín dụng. Sàng lọc sơ bộ có thể xét ngành nghề, thời gian hoạt động, quy mô doanh thu, lịch sử tín dụng, tình trạng pháp lý và mức độ phù hợp với khẩu vị rủi ro.

Thiết kế hệ thống:

- Human owner: RM/QHKH.
- Agent hỗ trợ: Relationship & Intake Agent chỉ chuẩn hóa prospect và thông tin sàng lọc có nguồn.
- Output: `Prospect`, `ProspectScreeningSnapshot`, `ContactDecision` do người dùng ghi.
- Human gate: `HG_PROSPECT_CONTACT_DECISION` trước mọi liên hệ.
- Không tự động scrape dữ liệu không được phép, chấm điểm bí mật hoặc tự liên hệ khách hàng.
- Vì chưa có khẩu vị rủi ro chính thức, mọi screening rule hiện tại chỉ là cấu hình synthetic có nhãn.

### Giai đoạn 2 — Tiếp nhận và xác định nhu cầu tín dụng

QHKH trao đổi để xác định:

- số tiền đề nghị;
- mục đích sử dụng vốn;
- thời hạn và phương thức cấp tín dụng;
- thời điểm cần vốn;
- nguồn và kế hoạch trả nợ;
- tài sản hoặc biện pháp bảo đảm dự kiến;
- sản phẩm liên quan như bảo lãnh, L/C, chiết khấu hoặc tài trợ thương mại.

Ngân hàng có thể hình thành cấu trúc tài trợ sơ bộ theo chu kỳ kinh doanh và nhu cầu vốn thực tế.

Thiết kế hệ thống:

- Relationship & Intake Agent kiểm tra completeness và consistency, không tự điền phần thiếu.
- `FinancingRequest` phải versioned và chứa tối thiểu amount, currency, purpose, product, term, expected-use date, repayment source/plan, proposed security, customer own funds, connected trade products, working-capital cycle, key suppliers/customers và proposed cash-flow controls khi các trường này thực sự được người dùng cung cấp.
- Trường thiếu giữ trạng thái `UNKNOWN`/`NOT_PROVIDED`, không dùng giá trị model suy đoán.
- Human gate: `HG_FINANCING_NEED_CONFIRMED` gắn đúng financing-request version.

### Giai đoạn 3 — Thu thập và kiểm tra hồ sơ

Ngân hàng thu thập hồ sơ pháp lý, tài chính, hoạt động kinh doanh, phương án sử dụng vốn, tài sản bảo đảm và tài liệu liên quan. Thông tin được đối chiếu với dữ liệu nội bộ, thông tin tín dụng, sao kê, thuế, hóa đơn, hợp đồng kinh tế và các nguồn hợp pháp khác.

Thiết kế hệ thống:

- Upload document-by-document qua backend-created upload intent; browser chỉ upload trực tiếp vào private Supabase Storage bằng signed/resumable operation có RLS.
- Mỗi re-upload tạo `DocumentVersion` immutable; không overwrite bản cũ.
- Pipeline: security validation → parse/OCR → classify → KIE/table/vision khi cần → candidate extraction → evidence region → embedding/index → officer review.
- Uploaded document là untrusted data. Prompt injection trong tài liệu không được trở thành system instruction hoặc tool request.
- Candidate fact không phải authoritative fact. Assigned intake officer disposition từng candidate bằng `ACCEPTED`, `CORRECTED`, `ABSENT` hoặc `UNREADABLE`; correction cần rationale và provenance.
- Confirmation phải atomic, audit được và bound vào document version/source region.
- Hệ thống phát hiện duplicate, expiry, low confidence, conflict và missing evidence bằng rule có version; không tự kết luận gian lận.
- Outbound document request do agent soạn nhưng chỉ gửi sau `HG_OUTBOUND_REQUEST_APPROVED`.
- Intake chỉ hoàn tất sau `HG_INTAKE_COMPLETE` và tạo immutable `IntakeHandoff` tới specialist roles.

### Giai đoạn 4 — Thẩm định khách hàng và đề nghị cấp tín dụng

Thẩm định bao gồm bảy nhóm nội dung:

1. Pháp lý: tư cách doanh nghiệp, đại diện, sở hữu, điều lệ, giấy phép, quyền vay và quyền dùng tài sản bảo đảm.
2. Hoạt động kinh doanh: mô hình, thị trường, ngành, năng lực quản lý, khách hàng, nhà cung cấp, cạnh tranh, chu kỳ và rủi ro hoạt động.
3. Tài chính: doanh thu, lợi nhuận, dòng tiền, tài sản/nguồn vốn, phải thu/phải trả, tồn kho, nghĩa vụ nợ, thanh khoản, đòn bẩy và hiệu quả.
4. Nhu cầu và mục đích vốn: tính thực tế, hợp pháp, phù hợp quy mô; đối chiếu số tiền đề nghị với tổng nhu cầu vốn và phần vốn tự có.
5. Khả năng trả nợ: nguồn trả nợ chính, thời điểm dòng tiền và độ khớp với lịch gốc/lãi; có thể dùng kịch bản doanh thu giảm, chi phí tăng, công nợ kéo dài hoặc thị trường biến động.
6. Khả năng thu hồi nợ: dòng tiền bổ sung, tài sản bảo đảm, bảo lãnh và biện pháp dự phòng.
7. Tài sản bảo đảm: ownership/use right, legal status, giá trị và nguồn định giá, transferability, liquidity và khả năng xử lý.

Tài sản bảo đảm là mitigant và nguồn thu dự phòng, không thay thế đánh giá khả năng trả nợ từ hoạt động kinh doanh.

Thiết kế hệ thống:

- Underwriting và Legal/Compliance/Collateral chạy song song sau intake handoff nhưng mỗi role thực hiện first pass độc lập.
- Underwriting dùng calculator deterministic cho ratio, reconciliation, working-capital need, debt service và scenarios.
- Legal role chỉ nêu `POTENTIAL_EXCEPTION`, `LEGAL_ISSUE_FOR_REVIEW` hoặc evidence gap; không đưa legal conclusion cuối cùng.
- Production KYC/AML/watchlist, CIC, registry, valuation hoặc core banking là OUT OF SCOPE; chỉ mock deterministic adapter có nhãn trong demonstration.
- Specialist human review: `HG_UNDERWRITING_ASSESSMENT_REVIEWED` và `HG_LEGAL_ASSESSMENT_REVIEWED`.

### Giai đoạn 5 — Lập và trình phương án cấp tín dụng

Đơn vị phụ trách lập tờ trình/báo cáo đề xuất. Nội dung nguồn mô tả gồm:

- thông tin khách hàng;
- hình thức, phương thức, số tiền/hạn mức;
- mục đích, thời hạn cho vay và thời hạn của từng khoản nhận nợ;
- đồng tiền cho vay/trả nợ;
- lãi suất, phí, cách tính lãi và kỳ trả lãi;
- lịch trả gốc và nguồn trả nợ;
- tài sản/biện pháp bảo đảm và tỷ lệ cấp tín dụng trên giá trị tài sản;
- điều kiện phê duyệt, điều kiện giải ngân;
- kiểm soát dòng tiền và quản lý khoản vay;
- cam kết tài chính/phi tài chính;
- vi phạm, chấm dứt cấp tín dụng hoặc thu hồi trước hạn;
- rủi ro và biện pháp kiểm soát.

Thiết kế hệ thống:

- Underwriting Agent tạo versioned `MakerCreditProposal` từ confirmed facts, deterministic outputs và specialist findings.
- Mỗi section chứa citations, assumptions, unresolved gaps và lineage.
- Agent không ghi decision language như “phê duyệt/từ chối”.
- Underwriter human review và submit qua `HG_MAKER_SUBMISSION_CONFIRMED`.

### Giai đoạn 6 — Thẩm định độc lập và phê duyệt tín dụng

Hồ sơ có thể được risk management, independent appraisal hoặc credit expert rà soát/tái thẩm định. Nguồn nêu các lựa chọn của cấp có thẩm quyền:

- phê duyệt theo đề xuất;
- phê duyệt có điều kiện;
- điều chỉnh số tiền, thời hạn hoặc cấu trúc;
- yêu cầu bổ sung tài sản bảo đảm;
- yêu cầu bổ sung thông tin/chứng từ;
- từ chối cấp tín dụng.

Quyết định phải tuân thủ thẩm quyền, giới hạn, khẩu vị rủi ro và quy định nội bộ; các nội dung này hiện chưa được cung cấp chính thức.

Thiết kế hệ thống:

- Risk Review chạy hai pass. Pass A đọc evidence, calculations và gaps nhưng chưa đọc maker conclusion để hình thành independent pre-analysis. Pass B so sánh maker/legal artifacts, tạo challenge và chỉ ra omission/disagreement.
- Challenge disposition gồm ít nhất `NOTED`, `ACCEPTED_RISK`, `MAKER_MUST_REVISE`, `ESCALATED`; ý nghĩa được cấu hình, không tự coi “đã disposition” là “được tiếp tục”.
- `MAKER_MUST_REVISE` tạo maker task version mới và checker rerun; không mở Credit Operations.
- Credit Operations chỉ assemble package sau khi challenge lifecycle hợp lệ; không phê duyệt.
- Human gates: `HG_RISK_CHALLENGES_DISPOSITIONED`, `HG_CREDIT_PACKAGE_FINALIZED`, `HG_CREDIT_DECISION_RECORDED`.
- `HumanCreditDecision` chỉ do actor có authority ghi, bound vào exact case/memo/assessment versions. Các label nghiệp vụ có thể gồm `APPROVED_AS_PROPOSED`, `APPROVED_WITH_CONDITIONS`, `RETURNED_FOR_REVISION`, `MORE_INFORMATION_REQUIRED`, `DECLINED_BY_HUMAN`; đây là PROPOSED taxonomy, phải cấu hình theo nguồn chính thức.

### Giai đoạn 7 — Thông báo tín dụng cho khách hàng

Sau quyết định của cấp có thẩm quyền, ngân hàng phát hành thông báo nêu số tiền, thời hạn, mục đích, lãi suất hoặc nguyên tắc xác định, tài sản bảo đảm và các điều kiện trước ký/giải ngân. Thông báo không đồng nghĩa ngân hàng phải giải ngân ngay.

Thiết kế hệ thống:

- Credit Operations Agent chỉ tạo `CreditNotificationDraft` từ HumanCreditDecision và approved terms.
- Không tạo hoặc gửi nếu decision chưa cho phép.
- Human gate `HG_CREDIT_NOTIFICATION_APPROVED` trước delivery.
- Delivery adapter là mock trong phạm vi hiện tại; lưu `CommunicationReceipt` và exact content hash.
- UI luôn hiển thị: “Thông báo tín dụng không phải xác nhận giải ngân.”

### Giai đoạn 8 — Đàm phán và ký kết hồ sơ tín dụng

Các văn bản có thể gồm hợp đồng tín dụng/cho vay, hạn mức, khế ước nhận nợ, bảo đảm/bảo lãnh, kiểm soát dòng tiền, quản lý tài sản, cam kết và phụ lục. Người ký phải có thẩm quyền theo pháp luật, điều lệ và quyết định nội bộ.

Thiết kế hệ thống:

- Deterministic template renderer tạo document từ approved-term snapshot; model không tự phát minh clause.
- Clause RAG chỉ được đọc approved, effective, versioned corpus.
- Legal review và redline được version hóa; material-change detector so sánh terms với HumanCreditDecision.
- Material change bắt buộc quay lại stage 6 và tạo decision version mới nếu cần.
- Human gates: `HG_CONTRACT_PACKAGE_APPROVED`, `HG_SIGNATURE_AUTHORITY_CONFIRMED`, `HG_CONTRACTS_SIGNED`.
- E-sign hoặc contract execution thật là OUT OF SCOPE; chỉ lưu mock signature evidence.

### Giai đoạn 9 — Hoàn thiện biện pháp bảo đảm

Khi áp dụng, ngân hàng và bên bảo đảm thực hiện công chứng, chứng thực và đăng ký biện pháp bảo đảm. Đăng ký không hoàn toàn đồng nghĩa phong tỏa; nó công khai quyền nhận bảo đảm và hỗ trợ xác lập hiệu lực đối kháng/thứ tự ưu tiên. Tùy tài sản và thỏa thuận, bên bảo đảm có thể tiếp tục sử dụng nhưng bị hạn chế giao dịch.

Thiết kế hệ thống:

- Mỗi tài sản có `SecurityInterest` và `SecurityPerfectionItem` riêng, không dùng một boolean chung.
- Theo dõi evidence, owner, authority, filing/reference, effective date, expiry và status per requirement.
- Không dùng LLM để định giá; chỉ lưu valuation reference do nguồn/adapter cung cấp.
- Agent không gọi registration API thật hoặc tuyên bố thứ tự ưu tiên cuối cùng.
- Human gate `HG_SECURITY_PERFECTION_CONFIRMED` với evidence receipt.

### Giai đoạn 10 — Kiểm tra điều kiện giải ngân

Tác nghiệp tín dụng độc lập với đơn vị kinh doanh kiểm tra hợp đồng đã ký, bảo đảm hoàn thiện, khách hàng đã tham gia đủ phần vốn tự có, chứng từ mục đích, giấy phép/hợp đồng, thay đổi đáng kể của khách hàng và điều kiện khác.

Thiết kế hệ thống:

- Dùng typed `ConditionLedger`; mỗi condition gắn source decision/contract, owner, due date, evidence, verifier, verification time và version.
- Status tối thiểu: `PENDING`, `EVIDENCE_SUBMITTED`, `VERIFIED`, `FAILED`, `WAIVER_REQUESTED`, `WAIVED_BY_HUMAN`, `SUPERSEDED`, `NOT_APPLICABLE_BY_HUMAN`.
- Model chỉ gợi ý mapping evidence; deterministic rule và human checker xác nhận.
- Waiver luôn human-only và phải có authority record.
- Human gate `HG_DISBURSEMENT_CONDITIONS_CONFIRMED` do ops checker độc lập thực hiện.

### Giai đoạn 11 — Giải ngân vốn vay

Nguồn yêu cầu giải ngân đúng số tiền, mục đích, thời hạn, phương thức và điều kiện; có thể chuyển cho beneficiary/supplier hoặc vào tài khoản khách hàng trên cơ sở chứng từ hợp lệ; phải kiểm soát đúng mục đích.

Thiết kế hệ thống:

- Agent chỉ tạo `ProposedDisbursementAction` từ approved terms và verified conditions.
- Hai human gates tách biệt: `HG_DISBURSEMENT_VALIDATED` và `HG_DISBURSEMENT_AUTHORIZED` theo maker–checker/authority policy.
- Deterministic mock adapter thực thi; không có real core-banking execution.
- Amount dùng exact decimal, currency-aware validation, beneficiary/account reference và idempotency key.
- Timeout hoặc mất response tạo `EXECUTION_UNKNOWN`; không blind retry. Human reconciliation xác định receipt trước thao tác tiếp.

### Giai đoạn 12 — Quản lý khoản vay và giám sát sau cấp tín dụng

Nguồn mô tả theo dõi sử dụng vốn, dòng tiền/doanh thu, báo cáo định kỳ, tồn kho/công nợ/tiến độ, tài sản bảo đảm, cam kết, tài chính, khả năng trả nợ, cảnh báo sớm, phân loại nợ và biện pháp quản trị rủi ro.

Thiết kế hệ thống:

- Post-Credit Monitoring Agent làm role hỗ trợ, không tự phân loại nợ chính thức hoặc áp dụng biện pháp.
- Longitudinal memory phân biệt `effectiveAt`, `observedAt`, `recordedAt`; không overwrite lịch sử.
- Deterministic schedule/rule engine tạo monitoring obligations và alert candidates.
- `MonitoringObservation`, `CovenantTest`, `EarlyWarningAlert` đều có evidence và lifecycle.
- Human reviewer disposition alert; agent chỉ summarize, explain và đề xuất follow-up.
- Production account feeds, collateral feeds và classification system là OUT OF SCOPE; dùng synthetic/mock input.

### Giai đoạn 13 — Thu nợ gốc, lãi và phí

Ngân hàng thu theo lịch. Nếu khả năng trả nợ suy giảm, có thể tăng kiểm soát dòng tiền, ngừng phần hạn mức chưa dùng, yêu cầu bổ sung bảo đảm hoặc áp dụng biện pháp quản lý nợ. Cơ cấu lại thời hạn trả nợ chỉ được thực hiện khi đáp ứng pháp luật và quy định nội bộ.

Thiết kế hệ thống:

- Deterministic `RepaymentLedger` tính schedule, allocation, outstanding principal, interest, fees và reconciliation bằng exact decimal.
- Collections & Recovery Agent nhận diện exception từ ledger/alerts, chuẩn bị contact/action proposal; không tự contact hoặc cấu trúc lại.
- Duplicate, reversal, partial, late, out-of-order và backdated payment phải được xử lý deterministic, idempotent và audit được.
- Restructuring proposal quay lại Underwriting → Risk Review → human decision; không mutate schedule trực tiếp.
- Mọi kiểm soát dòng tiền, dừng hạn mức hoặc yêu cầu bảo đảm là proposed action chờ human authorization.

### Giai đoạn 14 — Tất toán hoặc xử lý nợ

Nhánh bình thường: khi hoàn thành nghĩa vụ, ngân hàng tất toán, giải chấp và xóa đăng ký bảo đảm. Nhánh vi phạm không khắc phục: có thể thu hồi, xử lý tài sản, yêu cầu bảo lãnh, khởi kiện hoặc biện pháp pháp lý khác.

Thiết kế hệ thống:

- Nhánh 14A settlement chỉ mở khi deterministic ledger xác minh zero balance và mọi obligation được disposition.
- Human gate `HG_SETTLEMENT_CONFIRMED`; mock adapter tạo closure/release receipts.
- Nhánh 14B recovery chỉ được mở từ deterministic trigger cộng human escalation, không từ model score đơn lẻ.
- Recovery Agent chuẩn bị evidence pack, options, dependencies và consequences; Legal/human authority quyết định.
- Human gates riêng cho recovery strategy, security action, legal action và write-off nếu policy sau này cho phép.
- Real enforcement, registry release, litigation và write-off là OUT OF SCOPE.

## 6. Context pháp lý từ nguồn và cách sử dụng an toàn

Nguồn nêu các nhóm pháp lý thường liên quan:

1. Luật Các tổ chức tín dụng và văn bản sửa đổi/bổ sung.
2. Thông tư của Ngân hàng Nhà nước về cho vay, lãi suất, phân loại nợ, dự phòng, giới hạn, an toàn và quản trị rủi ro.
3. Bộ luật Dân sự và quy định về hợp đồng, nghĩa vụ, bảo lãnh, cầm cố, thế chấp, xử lý tài sản.
4. Luật Doanh nghiệp, điều lệ, nghị quyết và quyết định nội bộ về thẩm quyền vay/dùng tài sản bảo đảm.
5. Pháp luật về đăng ký biện pháp bảo đảm.
6. Luật Đất đai, Luật Nhà ở và văn bản liên quan khi có quyền sử dụng đất, nhà hoặc tài sản gắn liền với đất.
7. Luật Kinh doanh bất động sản khi khách hàng/khoản vay liên quan bất động sản.
8. Pháp luật đầu tư, xây dựng, thuế, kế toán, môi trường và chuyên ngành.
9. Quy định phòng, chống rửa tiền, nhận biết khách hàng và kiểm soát giao dịch.
10. Quy chế, quy trình, chính sách tín dụng, thẩm quyền và hướng dẫn nội bộ từng ngân hàng.

Không phải mọi văn bản áp dụng như nhau cho mọi khoản vay. Applicability phụ thuộc customer type, purpose, product, phương thức cấp tín dụng, collateral và industry. Danh sách này không chứa citation, effective version hoặc legal interpretation; vì vậy hệ thống không được hard-code nội dung pháp lý từ danh sách. Policy/legal RAG chỉ hoạt động trên corpus đã được human governance phê duyệt, versioned, access-controlled và có effective-date metadata. Khi không có corpus phù hợp, output phải là `POLICY_SOURCE_UNAVAILABLE` hoặc manual review, không dùng kiến thức model làm policy.

### 6.1 Ma trận traceability của 14 giai đoạn

Các gate trong bảng là PROPOSED application controls, không phải tên gate chính thức của SHB.

| # | Human owner chính | Agent hỗ trợ chính | Artifact/state đầu ra | Gate/điểm dừng bắt buộc |
|---|---|---|---|---|
| 1 | RM/QHKH | Relationship & Intake | Prospect, screening snapshot | `HG_PROSPECT_CONTACT_DECISION` |
| 2 | Assigned intake officer/RM | Relationship & Intake | FinancingRequest version | `HG_FINANCING_NEED_CONFIRMED` |
| 3 | Assigned intake officer | Relationship & Intake + Document Worker | Confirmed facts, conflicts, gaps, IntakeHandoff | `HG_OUTBOUND_REQUEST_APPROVED`, `HG_INTAKE_COMPLETE` |
| 4 | Underwriter và legal/collateral specialists | Underwriting; Legal/Compliance/Collateral | Calculations, two specialist assessments | Hai specialist-review gates |
| 5 | Human underwriter/maker | Underwriting | MakerCreditProposal version | `HG_MAKER_SUBMISSION_CONFIRMED` |
| 6 | Risk checker, operations assembler, authorized approver | Risk Review; Credit Operations | Challenges, final package, HumanCreditDecision | Risk disposition, package finalization, human credit decision |
| 7 | Authorized communication owner | Credit Operations | Notification draft và receipt | `HG_CREDIT_NOTIFICATION_APPROVED` |
| 8 | Legal reviewer và authorized signatories | Legal; Credit Operations | Contract package, redlines, signature evidence | Contract, authority và signing gates |
| 9 | Collateral/legal officer | Legal/Collateral; Credit Operations | Security perfection ledger/receipts | `HG_SECURITY_PERFECTION_CONFIRMED` |
| 10 | Independent credit-ops checker | Credit Operations | ConditionLedger | `HG_DISBURSEMENT_CONDITIONS_CONFIRMED` |
| 11 | Disbursement maker/checker/authorizer | Credit Operations chỉ chuẩn bị | Proposed action và mock execution receipt | Validation + authorization tách biệt |
| 12 | Monitoring officer | Post-Credit Monitoring | Obligations, observations, alerts | Human alert disposition/action authorization |
| 13 | Collections/operations officer | Collections & Recovery | Repayment ledger, reconciliation, proposed actions | Human authorization; restructure quay lại stage 4–6 |
| 14 | Settlement/recovery/legal authority | Collections & Recovery + Legal/Ops | Closure/release receipt hoặc RecoveryCase | Settlement/recovery/legal-action gates |

## 7. Kiến trúc target đã duyệt

```text
Vietnamese Next.js frontend on Vercel
  -> Cloud Run FastAPI API
  -> Supabase Postgres + RLS + pgvector
  -> Supabase Queues + private Storage
  -> Cloud Run worker/job entrypoints
  -> provider-neutral model gateway
  -> FPT AI Factory managed inference only
```

### 7.1 Ownership

- Vercel/frontend: render state, collect user intent, perform client validation, upload with backend intent; không giữ workflow authority.
- FastAPI: authentication mapping, authorization, validation, commands/queries, signed upload intents, human gates, proposed actions và response contracts.
- Cloud Run application layer: deterministic state machine, context builder, tools, orchestration, idempotency, outbox, worker composition.
- Supabase: authoritative records, RLS, queue identifiers, immutable object references, pgvector, checkpoints, audit/outbox.
- FPT: reasoning/document/vision/embedding/reranking inference; không giữ shared state, không execute tool, không authorize.

### 7.2 Tám logical agents

Tám role là boundary trách nhiệm, không bắt buộc tám model hoặc tám service:

| Role | Mục tiêu | Output chính | Cấm |
|---|---|---|---|
| Case Orchestrator | Lập/routing task theo deterministic graph | task, readiness, handoff, blocked/escalated state | phân tích chuyên môn hoặc quyết định tín dụng |
| Relationship & Intake | Chuẩn hóa need và evidence đầu vào | financing request, confirmations, gaps, intake handoff | bịa dữ liệu hoặc tự gửi request |
| Credit Underwriting | Maker analysis và cấu trúc đề xuất | calculations, findings, scenarios, proposal | approve/reject, dùng LLM thay calculator |
| Legal, Compliance & Collateral | Review legal/policy/collateral evidence | potential issues, citations, exceptions, gaps | legal conclusion cuối cùng, LLM valuation |
| Independent Risk Review | Independent checker/challenge | pre-analysis, challenges, dispositions requested | làm maker hoặc tự approve |
| Credit Operations | Assemble package và controlled proposals | memo/package, notifications, conditions, proposed actions | credit decision hoặc sensitive execution |
| Post-Credit Monitoring | Theo dõi obligations và signals | observations, covenant tests, alerts | official debt classification hoặc autonomous control |
| Collections & Recovery | Hỗ trợ thu nợ, settlement/recovery preparation | reconciliation exceptions, proposals, recovery pack | contact/action/restructure/enforcement tự động |

Evidence Gap Resolution là workflow capability dùng chung, không phải agent thứ chín.

## 8. Agent interaction protocol

Agent không chat trực tiếp để truyền state. Luồng chuẩn:

```text
Domain event or authorized command
  -> persist mutation + audit + outbox atomically
  -> Event Worker publishes opaque task identifier
  -> Orchestrator locks case/version and evaluates deterministic readiness
  -> task envelope is leased
  -> context builder creates scoped ContextManifest
  -> deterministic tools run first
  -> FPT receives minimum authorized context
  -> backend validates output schema, citations and tool requests
  -> versioned artifact + lineage + audit are persisted
  -> TASK_SUCCEEDED/TASK_FAILED event
  -> idempotent ORCHESTRATION_TICK
```

### 8.1 Durable task record và queue envelope

Durable task record trong Postgres cần:

- `taskId`, `idempotencyKey`, `taskType`, `assignedAgentRole`;
- `caseId`, `inputCaseVersion`, optional facility/loan/version refs;
- predecessor/dependency refs;
- scoped artifact/evidence refs, không chứa document body trong queue;
- `goalContractId`, expected output schema/version;
- authorized tools/capabilities;
- required human gate và block reason;
- attempt, lease owner/expiry, retry class;
- correlation/causation IDs, created/effective timestamps.

Queue envelope chỉ chứa opaque identifiers và contract version, ví dụ `taskId`, `caseId`, `caseVersion`, `taskType`, `idempotencyKey`, `correlationId`, `causationEventId`. Worker phải claim task rồi tải dependency, scope, lease, attempt, manifest và permission từ durable state; không nhét evidence refs, tool list, document body, secret hoặc customer payload vào queue message.

### 8.2 Output and handoff envelope

Output cần:

- execution/agent/profile/prompt/schema/model/tool versions;
- input case version và context hash;
- artifact refs, evidence refs, fact refs, tool-result refs;
- claims theo classification chuẩn;
- assumptions, uncertainty, gaps, conflicts, exceptions, challenges;
- output status và invalidation dependencies;
- token/latency/cost metadata không chứa sensitive content.

Handoff cần from/to role, artifact/version, readiness, unresolved refs, required human gate và `invalidatedBy` list. Handoff immutable; correction tạo version mới.

### 8.3 Tool calling

Model chỉ đề xuất structured tool call. Backend kiểm tra role allowlist, actor/case permission, case/artifact version, argument schema, idempotency và human authorization. Backend thực thi, lưu result, rồi model có thể nhận result đã lọc. Không model endpoint nào được credential trực tiếp tới Supabase, core/mocked action hoặc policy administration.

## 9. Workflow graph và sửa deadlock hiện tại

Graph hiện có theo code: Intake/G1 → Underwriting + Legal → G2 → Risk → G3 → Credit Operations. Nó có deadlock:

```text
Risk chờ G2 gap-request approval
G2 hiện chỉ thỏa bởi document request approval trong Credit Operations package
Credit Operations lại chờ Risk/G3
```

Thiết kế bắt buộc:

- tách `HG_OUTBOUND_REQUEST_APPROVED` thành gate/capability trước Risk, thuộc Evidence Gap workflow và actor có quyền duyệt giao tiếp;
- sau specialist assessments, deterministic gap assembler tạo versioned `GapRequestBatch` từ snapshot/hash của toàn bộ current open gaps và proposed outbound items;
- human disposition batch bằng `APPROVED_ALL`, `APPROVED_WITH_CHANGES`, `REJECTED` hoặc `NO_OUTBOUND_REQUESTS`; trường hợp không có request vẫn cần explicit `NO_OUTBOUND_REQUESTS`, không silent/vacuous satisfaction;
- gate chỉ `SATISFIED` khi batch và human disposition bind current case version, mọi outbound item được disposition và batch hash vẫn khớp current open-gap snapshot;
- gap/evidence/case version thay đổi làm batch cũ stale và gate mở lại cho version mới;
- migration/application change phải loại bỏ đường derive G2 từ `credit_ops_package` và `_maybe_satisfy_g2()` hiện tại, đồng thời backfill/test rõ legacy state;
- Credit Operations giữ approval cho package/proposed operational action sau Risk, không sở hữu pre-risk outbound request;
- orchestration tick tự chạy sau handoff, task completion, gap/conflict disposition, gate satisfaction, document version và human decision;
- `MAKER_MUST_REVISE` tạo feedback edge về Underwriting, sau đó Risk rerun trên version mới;
- chỉ rerun node bị invalidated, không restart toàn case;
- stale task không được ghi output vào case/document version mới hơn.

## 10. Shared memory và common context

Conversation memory không được dùng làm source of truth. Shared context gồm bảy lớp typed, versioned:

1. **Procedural memory:** agent profile, prompt, output schema, tool allowlist, workflow/policy configuration.
2. **Authoritative case memory:** confirmed facts, financing request, human decisions, approved terms, execution receipts.
3. **Evidence memory:** documents, versions, regions, passages, candidate facts, confirmations, provenance.
4. **Analytical memory:** calculations, assessments, findings, risks, mitigants, assumptions, challenges, responses, memo artifacts.
5. **Workflow memory:** tasks, dependencies, handoffs, gates, checkpoints, assignments, retries, invalidations.
6. **Longitudinal memory:** facilities, schedules, obligations, observations, alerts, payments, settlements/recovery cases.
7. **Policy memory:** approved policy corpus, versions, effective dates, access control, citations; luôn tách khỏi case evidence.

Không tạo một generic JSON “memory” table làm nguồn chuẩn. Dùng typed tables cho domain material; JSON chỉ dùng cho schema-versioned payload không material hoặc snapshot phục vụ audit/replay.

### 10.1 Goal hierarchy

- Case objective: mục tiêu nghiệp vụ tổng thể, human-owned.
- Stage objective: exit criteria của giai đoạn.
- Task objective: output contract cụ thể.
- Agent execution objective: bounded instruction cho một execution.

`GoalContract` immutable/versioned chứa objective, allowed actions, prohibited actions, success conditions, required evidence, output schema, budget và human gate. Agent không tự mở rộng goal.

### 10.2 ContextManifest

Mỗi model call phải có persisted manifest gồm:

- case/facility/document/artifact exact versions;
- user/role/assignment authorization snapshot;
- agent profile, prompt, schema, model và tool versions;
- goal contract và stage/task objective;
- authoritative fact refs và human-decision refs;
- upstream artifact/handoff refs;
- gaps, conflicts, challenges và unresolved questions;
- retrieval query/hit/passage refs;
- deterministic tool-result refs;
- explicit exclusions: stale, unauthorized, superseded hoặc outside-budget items;
- token/input/output/cost budgets;
- stable ordering và `contextHash`.

Context builder sequence:

1. authorize actor/service identity và case assignment;
2. lock exact case version;
3. load goal contract;
4. load authoritative facts/human decisions;
5. load allowed upstream handoffs;
6. load unresolved gaps/conflicts/challenges;
7. run deterministic facts/calculations needed;
8. run graph-guided retrieval;
9. exclude stale/unauthorized/superseded content;
10. pack to budget by priority and persist manifest/hash;
11. call FPT and validate output.

System instruction, role instruction, schema và tool protocol phải nằm ở trusted sections; document text luôn nằm ở untrusted evidence section.

## 11. Kế thừa reasoning mà không lưu chain-of-thought

Không lưu hoặc truyền hidden chain-of-thought. Kế thừa qua structured claims và derivation graph:

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

Claim types:

- `OBSERVATION`
- `CONFIRMED_FACT_REFERENCE`
- `CALCULATION_RESULT`
- `INFERENCE`
- `ASSUMPTION`
- `RISK`
- `MITIGANT`
- `POTENTIAL_EXCEPTION`
- `CHALLENGE`
- `PROPOSED_ACTION`

Mỗi claim có author role/execution, statement hoặc structured value, evidence/tool refs, derivation refs, uncertainty, status, applicable case version và invalidation conditions. Downstream agent phải khai báo disposition với inherited claims: independently verified, accepted conditionally, challenged, rejected, superseded, new claim hoặc unresolved. Không copy/average confidence một cách cơ học. Khi upstream evidence stale, downstream artifacts bị đánh dấu stale theo dependency graph nhưng không bị xóa.

## 12. Graph-guided hybrid RAG

Không triển khai full GraphRAG community/global search cho case-level workflow ban đầu. Dùng typed EvidenceGraph trong Postgres + pgvector; graph thu hẹp phạm vi rồi RAG về tài liệu gốc.

### 12.1 Ba graph tách biệt

- Case Evidence Graph: document, region, fact, conflict, gap, calculation, finding, claim, artifact.
- Policy Graph: policy document, version, provision, applicability, exception, supersession.
- Analytical Reasoning Graph: claim, derivation, challenge, response, human disposition, invalidation.

Không trộn policy corpus với customer evidence. Mọi node/edge có tenant/case scope, version, provenance, visibility và effective timestamps.

### 12.2 Retrieval pipeline

```text
task/query
  -> authorization + tenant/case/version/effective-date filters
  -> resolve typed seed nodes
  -> bounded graph traversal by allowed edge types/hops/node count
  -> candidate source-region references
  -> lexical + vector retrieval over original passages
  -> optional rerank only if benchmark proves value
  -> source hydration from immutable document version
  -> deduplicate and token-pack
  -> FPT inference
  -> citation validator rejects unsupported claims
```

Graph không thay tài liệu gốc. Mọi citation material phải trả về document/policy ID, version, page/sheet/region, passage hash và access scope. Không retrieve cross-case. Stale/superseded version bị filter trước vector search. Global/community GraphRAG chỉ có thể xem xét sau cho portfolio analysis nếu có benchmark và data governance; không dùng cho decision support từng hồ sơ.

### 12.3 Token efficiency

Ưu tiên theo thứ tự:

1. SQL/filter typed facts.
2. Deterministic calculators/rules.
3. Bounded graph traversal.
4. Lexical/vector retrieval.
5. Optional reranker.
6. LLM với minimum context.

Budget phân tầng environment → case → stage → task → retrieval → output. Cache theo case/artifact/query/retrieval/model/prompt/schema versions; invalidation theo version, không TTL mơ hồ.

## 13. Domain model bổ sung

Ngoài schema hiện có, master design cần các aggregate/table family sau. Tên cuối cùng phải theo naming conventions hiện có; đây là semantic contract:

- agent governance: `agent_profiles`, `goal_contracts`, `agent_executions`, `agent_context_manifests`, `context_artifact_refs`;
- retrieval: `retrieval_queries`, `retrieval_hits`, `evidence_passages`, typed `evidence_edges`;
- workflow: `workflow_domain_events`, `outbox_events`, `human_gates`, `human_gate_dispositions`, `role_assignments`;
- decisions: `human_credit_decisions`, `approved_term_snapshots`;
- stage 1–3: prospects, screening snapshots, financing request versions, confirmations, conflicts, gaps, outbound requests, intake handoffs;
- stage 4–6: calculations, underwriting/legal assessments, claims, challenges, maker responses, memo/package versions;
- stage 7–11: notifications, communications, contract packages, redlines, signature evidence, security interests/perfection items, condition ledger, proposed disbursements, authorizations, execution receipts;
- stage 12–14: facilities, monitoring obligations/observations/alerts, covenant tests, repayment schedules/events/allocations, collection cases, settlement checks, recovery cases/actions.

Mỗi material row cần tenant/case scope, stable ID, version hoặc immutable semantics, created/recorded/effective timestamps, actor/service identity, correlation/causation, provenance và soft invalidation/supersession thay vì destructive overwrite.

## 14. Domain event, outbox, queue và worker

### 14.1 Event envelope

Mỗi domain event chứa event ID/type/schema version, aggregate type/ID/version, tenant/case/version, actor type/ID/role, correlation/causation IDs, effective/recorded timestamps và schema-validated payload. Không đưa raw document hoặc secret vào event/queue.

### 14.2 Transactional outbox

Mọi command material commit trong một transaction:

1. domain mutation;
2. versioned artifact/lineage;
3. audit record;
4. outbox event.

Event worker publish idempotent; consumer ghi `inbox_receipt` và deduplicate bằng `(consumer, eventId)` cùng task/idempotency key. Outbox xử lý dual-write window giữa database commit và queue send: transaction chỉ tạo domain state + outbox; dispatcher riêng mới publish. Recovery sweep thuộc Event worker tìm outbox/lease bị stranded và không chạy model.

### 14.3 Bốn worker modes

- Document worker/queue: security, parse, classify, extract, index.
- Agent worker/queue: context build, deterministic tools, FPT, schema/citation validation, artifact persistence.
- Event worker/queue: outbox publish, dependency invalidation, orchestration tick, notifications nội bộ.
- Action worker/queue: only authorized mock actions, receipt/reconciliation.

Có thể cùng container image nhưng tách entrypoint, queue, service account, timeout, concurrency và permissions.

### 14.4 Checkpoint/retry

- Stable task ID và idempotency key.
- Lease có expiry; crash làm message available lại.
- Persist checkpoint/output transactionally; ack chỉ sau durable success.
- Duplicate delivery là bình thường và không tạo duplicate effect.
- Retry tiếp tục từ valid checkpoint.
- Read/model calls có bounded retry; persistence chỉ retry idempotent; external effect không blind retry.
- Schema-invalid output có bounded repair attempt rồi `FAILED_MANUAL_REVIEW`.
- FPT unavailable phải pause/manual-review rõ ràng; không hidden provider fallback nếu chưa được cho phép.
- Partial document processing không tạo confirmed fact/ready handoff.
- Cloud scheduler recovery sweep tìm stranded lease/task.

## 15. API design

Backend là contract authority. API versioned, idempotency-aware, capability-driven và fail closed. Nhóm resource đề xuất:

- `/cases`, `/cases/{id}/assignments`, `/cases/{id}/capabilities`;
- `/prospects`, `/prospects/{id}/contact-decisions`;
- `/cases/{id}/financing-request/versions`, `/confirm`;
- `/cases/{id}/upload-intents`, `/documents`, `/documents/{id}/versions`;
- `/document-versions/{id}/review`, `/candidate-facts/{id}/dispositions`;
- `/cases/{id}/facts`, `/conflicts`, `/gaps`, `/document-requests`;
- `/cases/{id}/intake-completion`, `/handoffs`;
- `/cases/{id}/orchestration/status`, internal orchestration tick endpoint hoặc event-only handler;
- `/cases/{id}/underwriting`, `/legal-assessments`, `/risk-reviews`, `/challenges`;
- `/cases/{id}/credit-packages`, `/human-credit-decisions`;
- `/cases/{id}/notifications`, `/contract-packages`, `/security-interests`, `/conditions`;
- `/cases/{id}/proposed-disbursements`, `/authorizations`, `/execution-receipts`;
- `/facilities/{id}/monitoring`, `/alerts`, `/repayment-ledger`, `/collections`, `/settlement`, `/recovery`;
- `/cases/{id}/evidence-lineage`, `/retrieval-traces`, `/audit-events`.

Mutation phải nhận expected version/ETag và idempotency key khi phù hợp. Human gate command luôn yêu cầu actor authority từ server-side claims; frontend không được tự gán role.

Error contract ổn định:

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

## 16. Backend module boundaries

Giữ kiến trúc domain/application/ports/infrastructure hiện có. Không đặt banking logic trong route hoặc React component.

- `domain/`: typed entities, invariants, transitions, value objects; không phụ thuộc provider.
- `application/`: use cases, context builder, orchestration, deterministic services/processors.
- `application/ports/`: repositories, queue, storage, model, policy, action adapter contracts.
- `infrastructure/postgres/`: repository implementations và transaction/UoW.
- `infrastructure/supabase/`: queue/storage adapters.
- `infrastructure/fpt/`: provider gateway/catalog; không chứa agent goal/rule.
- `infrastructure/mock/`: mọi external banking behavior hiện tại, có synthetic label.
- `api/`: auth, commands/queries, response/error mapping.
- `worker/`: composition roots cho bốn modes; không chứa domain logic.
- `prompts/`: versioned role prompts, schema-linked, untrusted-data boundary rõ.

Current blockers phải giải quyết trước khi mở rộng lifecycle:

1. `worker/main.py` chưa có real composition root và hiện fail nếu dependency chưa inject.
2. Chưa có concrete document-ingestion persistence processor end-to-end.
3. Thiếu FastAPI review/confirmation/evidence/conflict/gap/handoff/audit endpoints.
4. Thiếu intake completion/handoff application path.
5. G2 deadlock như mục 9.
6. Chưa tự orchestration tick sau domain success.
7. Queue publish chưa nối chắc chắn với Cloud Run Job dispatch.
8. Một số queue chưa có production poll policy.
9. `MAKER_MUST_REVISE` chưa tạo feedback branch đầy đủ.
10. Chưa có multi-role assignments, HumanCreditDecision và authority model đầy đủ.
11. Model catalog hiện có thể activate configured FPT route khi có endpoint/key mà chưa enforce benchmark-pass record; cần capability registry/benchmark gate trước khi coi route là `ACTIVE`.

## 17. Frontend operating model

Giao diện tiếng Việt trước. Người dùng thao tác trên task/artifact/human gate, không “điều khiển agent bằng chat” như nguồn chuẩn.

### 17.1 Global navigation

- Hàng việc của tôi
- Khách hàng tiềm năng
- Hồ sơ tín dụng
- Giám sát danh mục
- Thu nợ
- Tất toán
- Xử lý nợ
- Báo cáo vận hành
- Nhật ký kiểm toán
- Cấu hình

Route map PROPOSED, giữ các route hiện có khi phù hợp và mở rộng theo resource:

| Route | Workspace |
|---|---|
| `/cong-viec` | Hàng việc theo server capabilities, assignment và human gate |
| `/co-hoi` | Prospect và contact decision của giai đoạn 1 |
| `/ho-so`, `/ho-so/tao-moi` | Danh sách và tạo hồ sơ/financing request |
| `/ho-so/[caseId]/tong-quan` | Tóm tắt version, stage, blocker và next human action |
| `/ho-so/[caseId]/tiep-nhan` | Nhu cầu tín dụng và intake completion |
| `/ho-so/[caseId]/tai-lieu/[documentId]` | Review từng tài liệu/source region/candidate |
| `/ho-so/[caseId]/doi-chieu` | Fact ledger và conflicts |
| `/ho-so/[caseId]/khoang-trong` | Gap lifecycle và outbound request approval |
| `/ho-so/[caseId]/tham-dinh` | Underwriting/calculations/maker proposal |
| `/ho-so/[caseId]/phap-che` | Legal/compliance/collateral assessment |
| `/ho-so/[caseId]/rui-ro` | Independent review/challenges/dispositions |
| `/ho-so/[caseId]/tong-hop` | Credit Operations package |
| `/ho-so/[caseId]/phe-duyet` | Human Credit Decision trên exact artifact version |
| `/ho-so/[caseId]/thong-bao` | Notification draft/approval/receipt |
| `/ho-so/[caseId]/hop-dong` | Contracts/redlines/signature evidence |
| `/ho-so/[caseId]/bao-dam` | Security perfection ledger |
| `/ho-so/[caseId]/dieu-kien-giai-ngan` | Condition ledger |
| `/ho-so/[caseId]/giai-ngan` | Proposed action/authorization/reconciliation |
| `/ho-so/[caseId]/giam-sat` | Obligations/observations/alerts |
| `/ho-so/[caseId]/thu-no` | Repayment ledger/collections |
| `/ho-so/[caseId]/tat-toan-xu-ly-no` | Settlement hoặc recovery case |
| `/ho-so/[caseId]/quy-trinh` | Task graph, executions, gates và deadlock state |
| `/ho-so/[caseId]/ban-giao` | Immutable handoffs |
| `/ho-so/[caseId]/nhat-ky` | Cursor-paginated audit timeline |
| `/bao-cao-van-hanh` | Metrics/task/queue/gate operations; chỉ render khi có reporting capability |
| `/nhat-ky-kiem-toan` | Cross-case audit search cho auditor có quyền |
| `/cau-hinh` | Versioned workflow/policy/model configuration cho admin có quyền; không chỉnh banking rule nếu thiếu source |

`/cong-viec` là entry mặc định. Query role chỉ lọc trong capability server đã cấp, không cấp thêm quyền. Mỗi work item hiển thị case/artifact version, owner, lý do giao, blocker/severity, unresolved counts, stale/manual-review status và một primary action dẫn đúng workspace.

### 17.2 Case workspace

Nhóm phase:

- Chuẩn bị và quyết định: giai đoạn 1–6.
- Hợp đồng và giải ngân: giai đoạn 7–11.
- Sau cấp tín dụng: giai đoạn 12–14.

Panels dùng chung:

- Tóm tắt hồ sơ và version hiện tại.
- Nguồn chứng cứ/source viewer.
- Fact ledger, gaps và conflicts.
- Hoạt động agent và task/checkpoint.
- Structured lineage, không hiển thị chain-of-thought.
- Human gates và decision history.
- Versions/stale/invalidation.
- Audit timeline.

### 17.3 Role work queues

- Trong prototype hiện tại, chỉ `Assigned Intake Officer` của case được mutate financing request, disposition candidate, conflict/gap intake, outbound request draft/approval khi có capability phù hợp và intake handoff. Việc đổi assignment hoặc delegation phải là command riêng có audit; không được suy từ client state.
- Assigned Intake Officer: prospect, financing need, upload/review từng document, candidate disposition, conflict/gap, outbound request và intake handoff.
- Underwriter: confirmed facts, calculator outputs, analysis, structure, maker proposal, challenge response.
- Legal/Collateral reviewer: legal evidence, exact policy citations, potential exceptions, collateral completeness.
- Risk reviewer: blind pass, maker comparison, challenge lifecycle, escalation.
- Authorized approver: neutral decision screen trên exact version, không preselect recommendation.
- Credit Operations Maker: assemble package, notification/contract drafts, security/condition preparation và proposed disbursement.
- Credit Operations Checker: độc lập verify conditions, package/action inputs; không cùng actor/execution với maker trên cùng artifact.
- Action Authorizer: human authority tách biệt để authorize mock disbursement hoặc controlled action; exact official claims là OPEN QUESTION và mọi mutation fail closed nếu role mapping chưa được cấu hình.
- Monitoring officer: obligations, observations, alert review.
- Collections/Recovery officers: ledger exceptions, proposed contacts/actions, settlement/recovery packs.
- Auditor: read-only trace và export có kiểm soát.

Server trả `capabilities`; frontend không suy role từ route, local storage hoặc label.

### 17.4 Human gate UX

- Mỗi gate là màn hình/flow riêng với artifact version, evidence, unresolved gaps/conflicts/challenges và authority requirement.
- Không default/preselect approval.
- User phải nhập rationale khi policy yêu cầu.
- Submit không optimistic-update material decision; chờ server receipt/version.
- Stale version trả conflict và yêu cầu reload/review lại.
- Action nguy hiểm hiển thị consequence, idempotency status và reconciliation state.

### 17.5 UI state

Mỗi màn hình phải có loading, empty, error, unauthorized, unassigned, contract-pending, stale, superseded, retry-wait, manual-review và partial-processing state. Không dùng mock fallback âm thầm khi API fail. Source viewer chỉ highlight khi có real derived-artifact/region contract; nếu chưa có phải ghi rõ chưa khả dụng.

- `202`: hiển thị queued/running, bounded polling và manual refresh.
- `401`: session expired; giữ draft nhạy cảm trong memory của tab, không browser storage.
- `403`: không đủ role; không render/enable mutation control.
- `404`: không phân biệt resource không tồn tại với case không được assignment.
- `409`: giữ in-memory draft, cấm auto-resubmit và yêu cầu reload/review version mới.
- `429`: tôn trọng `Retry-After`, không retry vô hạn.
- Retryable `5xx`: retry theo section, không che section đã tải thành công.
- Unknown enum/schema: fail closed với trạng thái chưa được hỗ trợ, không suy diễn success.
- `RETRY_WAIT`, `FAILED_MANUAL_REVIEW`, `SUPERSEDED`: có thông điệp, next action và mutation policy riêng.

Routes giai đoạn 7–14 chỉ được bật khi typed backend contract và mock adapter tương ứng đã được duyệt. Trước đó UI phải hiển thị `Ngoài phạm vi prototype hiện tại`, không hard-code dữ liệu nghiệp vụ để làm route trông như đang hoạt động.

## 18. Model và FPT inference

Provider contract phải capability-based và benchmark-gated. Code/catalog hiện có thể chứa candidate direction:

- main reasoning candidate: DeepSeek-V4-Flash;
- document vision candidate: Qwen2.5-VL-72B-Instruct;
- semantic retrieval candidate: multilingual-e5-large;
- optional reranker: bge-reranker-v2-m3;
- managed KIE/table extraction endpoint vẫn cần xác nhận/pin.

Tài liệu decision log đồng thời giữ Qwen3-30B-A3B, SaoLa3.1-medium 32B, FPT KIE/Table, Qwen2.5-VL-7B, FPT.AI-e5-large và Vietnamese_Embedding là các candidate/challenger. Đây không phải mâu thuẫn cần chọn bằng cảm tính: final selection vẫn là **OPEN QUESTION** và phải qua Vietnamese banking/document benchmark, structured-output/tool-call reliability, citation grounding, context limit, latency, throughput, endpoint availability, data controls, retention và cost.

Không fine-tune trước khi baseline + RAG + deterministic tools + prompt/schema evaluation chứng minh gap ổn định. Fine-tuning chỉ xem xét sau khi có approved representative dataset, data governance, train/eval split, measurable target và rollback. Không fine-tune trên customer documents tùy tiện.

Nếu benchmark chứng minh cần specialized model, fine-tuning là một wave MLE riêng: ưu tiên FPT managed fine-tuning/managed endpoint nếu provider và governance cho phép, không tái đưa GPU self-host vào kiến trúc. Dataset, base model, training config, evaluation run, model artifact, endpoint route và rollback đều phải versioned; model mới chỉ `ACTIVE` sau khi vượt baseline trên holdout và safety suites.

## 19. Security, privacy và safety controls

- Tenant/case RLS và server authorization cho mọi read/write.
- Assigned officer là minimum access, không thay thế multi-role assignment.
- Separate human role và agent/service role.
- Short-lived upload operation, private storage, immutable object naming và post-upload verification.
- File type/size/hash/security scan; parser sandbox/resource limit; archive/macro/external-reference handling.
- Prompt injection isolation; document text không thể thay system/tool policy.
- Secrets ở approved secret manager; không log prompt/document/raw provider response chứa sensitive content.
- Strict log redaction và separate audit store.
- Egress allowlist/provider endpoint validation, timeout, circuit breaker và request size limit.
- Backup/restore cho database và Storage objects là hai vấn đề riêng; chưa được coi là hoàn tất.
- Real data, production KYC/AML/CIC/registry/core-banking/e-sign/disbursement/recovery integrations đều bị chặn cho tới governance approval.

## 20. Observability và hiệu quả

Theo dõi:

- task/queue wait, lease, retry, duplicate và dead-letter/manual-review;
- document stage latency và extraction/citation quality;
- orchestration transition, blocked duration và human-gate age;
- FPT endpoint/model/version, token/input/output, latency, error, schema-repair rate;
- retrieval precision/recall, graph candidate size, reranker gain, citation validation;
- cost per document/case/stage/agent;
- stale/invalidation/rerun count;
- action authorization/execution/reconciliation;
- P50/P95/P99 latency và throughput theo measured environment.

Operational logs không thay audit. Audit phải trả lời ai/lúc nào/trên version nào/đã xem hoặc thay đổi gì/vì sao/nguồn nào.

## 21. Testing và acceptance

### 21.1 Test layers

- Domain unit tests cho invariants, transitions, exact decimals và temporal logic.
- Contract/schema tests cho API, task/event/model/tool envelopes.
- Repository/RLS tests chống cross-tenant/cross-case leakage.
- Worker tests cho lease, checkpoint, duplicate delivery, crash recovery và stale writes.
- Retrieval/citation tests với known evidence regions và superseded versions.
- Agent evaluation cho grounding, abstention, gap detection, structured output và prohibited action.
- Integration tests với local/test Supabase, mock FPT và mock external adapters.
- E2E role tests cho mỗi human workflow/handoff/gate.
- Security tests cho upload, injection, authorization, log redaction và malicious documents.
- Failure-injection tests cho FPT timeout, malformed output, queue redelivery, DB conflict và action unknown.
- Frontend component tests cho loading/empty/unauthorized/contract-pending/stale/superseded/manual-review, capability visibility, `409` draft preservation, keyboard/focus/semantic landmarks/live regions.
- BFF tests cho Origin/CSRF, HttpOnly workforce cookie, path/method allowlist, no direct FPT, no service-role credential và no secret/signed URL trong browser storage.
- Frontend typecheck, lint, unit/component tests và production build.
- Live managed FPT synthetic smoke/evaluation gate trên holdout; ghi endpoint/model/prompt/schema versions và cấm fallback. Chưa chạy gate này thì không claim inference hoạt động end-to-end.

### 21.2 Required synthetic scenarios

- complete clean case;
- missing evidence theo versioned synthetic checklist;
- conflicting documents/facts;
- duplicate/expired/unreadable/poor-scan document;
- table and multi-page extraction;
- potential policy exception;
- maker/checker disagreement and `MAKER_MUST_REVISE` loop;
- new evidence invalidating selected downstream artifacts;
- unauthorized officer/cross-case access;
- notification/contract material change;
- incomplete synthetic conditions và waiver attempt đối với synthetic policy/condition;
- duplicate/timeout/unknown disbursement action;
- monitoring alert lifecycle;
- partial, late, duplicate, reversal, out-of-order and backdated payments;
- normal settlement and recovery escalation branches.

### 21.3 Non-negotiable acceptance criteria

- Zero cross-case leakage in authorization/retrieval tests.
- No unconfirmed candidate becomes authoritative fact.
- Every material claim has valid evidence/tool lineage or explicit unsupported/uncertain status.
- No agent can record human credit decision, waive condition or authorize sensitive action.
- Maker–checker separation enforced by role, actor and artifact version.
- No customer communication or mock disbursement executes without exact human authorization.
- No blind retry after ambiguous external-effect result.
- No hidden model/provider/API fallback.
- No stale/superseded source returned as current.
- No hidden chain-of-thought stored or displayed.
- Every state-changing material command is idempotent and audit-linked.

Benchmark pass thresholds remain OPEN QUESTION and must be recorded before readiness claims.

## 22. Delivery decomposition

Master design này quá lớn cho một implementation plan an toàn. Sau khi người dùng duyệt spec, phải chia thành các plan có checkpoint và acceptance riêng:

1. Wave 0 — truth alignment: sửa status docs, role/state vocabulary, OpenAPI ownership, official-vs-synthetic labels.
2. Wave 1 — runtime foundation: composition roots, worker modes, outbox/events, auto orchestration, role assignments, audit/error contracts.
3. Wave 2 — giai đoạn 1–3: prospect, financing request, document review/confirmation, gaps/conflicts/request/handoff; sửa G2.
4. Wave 3 — giai đoạn 4–6: calculators, specialist review, two-pass risk, revision loop, package, HumanCreditDecision.
5. Wave 4 — giai đoạn 7–10: notification, contract versioning/redline, security perfection, condition ledger.
6. Wave 5 — giai đoạn 11: proposed disbursement, dual human authorization, mock action/reconciliation.
7. Wave 6 — giai đoạn 12: temporal monitoring, obligations, observations, alerts.
8. Wave 7 — giai đoạn 13–14: repayment ledger, collections, settlement và recovery preparation.
9. Wave 8 — hardening: RLS/security, retrieval evaluation, failure injection, performance/cost, backup/restore evidence, deployment gates.

Không bắt đầu wave sau khi P0 acceptance của wave trước chưa đạt. Mỗi wave phải đọc lại spec này, repository state, `OPEN_QUESTIONS.md` và `DECISION_LOG.md` tại commit thực thi.

## 23. Trạng thái repository tại thời điểm thiết kế

Mô tả chính xác, tránh hai cực “chưa có gì” và “đã production-ready”:

**CONFIRMED FROM CODE:** đã có local implementation của domain contracts, migrations, state machine/orchestration primitives, task/lease/checkpoint logic, upload flow, pure document-processing stages, FPT gateway, Underwriting/Legal/Risk/CreditOps processors và các frontend workbench liên quan.

Frontend detail: intake/document/evidence UI đã có nhưng FastAPI review/confirmation/evidence/conflict contracts chưa kín; gaps/handoff/audit render explicit contract-pending; orchestration, underwriting, legal, risk và credit-ops workbenches có local code; routes giai đoạn 7–14 chưa tồn tại và không được hard-code runtime data.

**CONFIRMED MISSING:** chưa có runnable end-to-end worker composition với dependency thật; chưa provision Vercel/Supabase/Cloud Run/FPT; chưa có live FPT smoke; thiếu nhiều API intake/evidence; chưa có official SHB workflow/policy/checklist/authority/API sandbox; chưa production-ready.

**CONFIRMED RUNTIME GAP:** code hiện có sáu task types lõi cho document, orchestrator, underwriting, legal/compliance/collateral, independent risk và credit operations; `GoalContract`, `ContextManifest`, reasoning-inheritance contract, transactional outbox và inbox dedup chưa hiện hữu trong repo và vẫn là target design.

**DOCUMENTATION CONFLICT:** `AGENTS.md`/một số docs nói “no prototype/runtime”, trong khi code và `PROJECT_CONTEXT.md` ghi local walking skeleton. Cách diễn đạt chuẩn là đoạn trên; cần sửa consistency ở Wave 0, không xóa lịch sử quyết định.

P0 trước khi claim end-to-end stages 2–6:

- worker composition root;
- concrete document persistence processor;
- review/confirmation/evidence/conflict/gap/handoff/audit APIs;
- intake completion/handoff;
- G2 deadlock;
- auto orchestration tick;
- reliable queue-to-Cloud-Run dispatch/poll policy;
- maker revision loop;
- multi-role authority và HumanCreditDecision.
- canonical synthetic notice chưa thống nhất giữa frontend và Credit Operations memo/domain schema.
- runtime chưa enforce benchmark-pass record trước khi activate candidate FPT route.

## 24. Open questions không được Claude tự trả lời

- Official SHB role names, RACI, SoD và delegation of authority.
- Official 14-stage case states, transitions, exit criteria, SLA/escalation.
- Checklist/document validity/expiry/certification/translation rules per product/customer.
- Risk appetite, materiality, limits và blocking conditions.
- Policy exception and waiver lifecycle.
- Credit memo, notification, contract và approval artifact formats.
- Conditions được phép outstanding tại approval/signing/disbursement.
- Official policy corpus, source hierarchy, effective dates và access controls.
- Invalidation mapping khi evidence mới đến.
- Identity provider, SSO, assignment lifecycle và periodic access review.
- Exact FPT endpoints, region, quota, context, retention, private connectivity và benchmark thresholds.
- Supabase/GCP/Vercel/FPT data residency/cross-border flow, backup, RPO/RTO và retention.
- LOS/ACAS/core-banking/CIC/KYC/AML/registry/e-sign integrations và read/write authority.
- Evaluation metrics judges prioritize và pass thresholds.
- Authorization để dùng bất kỳ real customer/banking data nào.

## 25. Context contract dành cho Claude Code

Khi chuyển tài liệu này cho Claude Code, phải đặt các instruction sau ở đầu task context:

1. Context này chỉ cung cấp guardrails; không tự cho phép thay đổi file, triển khai/provision cloud, tạo secret, dùng real data hoặc thực hiện hành động ngoài task hiện tại.
2. Đọc `AGENTS.md`, spec này, `docs/OPEN_QUESTIONS.md`, `docs/DECISION_LOG.md`, relevant docs/migrations/code và latest git status trước khi sửa.
3. Xem Credit Case Digital Twin là source of truth; conversation chỉ là interface.
4. Phân loại mọi requirement theo CONFIRMED/PROPOSED/ASSUMPTION/OPEN QUESTION/OUT OF SCOPE.
5. Không biến quy trình 14 bước tổng quát thành official SHB rule.
6. Không implement material banking threshold/checklist/authority chưa có nguồn; dùng versioned configuration hoặc fail closed.
7. Không cấp quyền approve/reject/waive/sign/disburse/restructure/release/enforce cho agent.
8. Dùng deterministic code cho calculation, rule, transition, authorization và action.
9. Mọi material output phải có evidence/version/provenance và stale/invalidation semantics.
10. Không lưu chain-of-thought; chỉ lưu structured claims, derivations, challenges và dispositions.
11. Graph-guided retrieval phải hydrate tài liệu gốc và validate citation; cấm cross-case retrieval.
12. FPT chỉ inference sau provider-neutral gateway; không hard-code model làm workflow authority.
13. Chỉ dùng synthetic data và giữ exact disclaimer; mọi external banking integration là mock có nhãn.
14. Không claim production readiness, regulatory compliance, official SHB policy, SHB approval hoặc endorsement.
15. Bảo toàn user changes/untracked files; không reset hoặc refactor ngoài scope.
16. Viết test trước hoặc đồng thời với behavior, chạy targeted rồi full verification; không claim complete nếu chưa có command evidence.
17. Mỗi implementation task phải nhỏ, có exact files, dependencies, acceptance tests, rollback và review checkpoint.
18. Nếu spec và code xung đột, báo conflict và impact; không âm thầm chọn một material interpretation.

Spec này cung cấp target architecture và constraints. Nó không cho phép Claude Code triển khai toàn bộ 14 giai đoạn trong một thay đổi khổng lồ. Claude phải làm theo các implementation plan được duyệt từng wave và dừng ở review checkpoint.

## 26. Definition of design done

Design được coi là được người dùng duyệt khi người dùng xác nhận bản spec này phản ánh đúng:

- đầy đủ 14 giai đoạn nghiệp vụ;
- tám logical agent và human ownership;
- backend/frontend operating model;
- shared memory, reasoning inheritance và Graph-guided RAG;
- human-only decision/action boundaries;
- current-code blockers và phased decomposition;
- synthetic-only/no-production/no-official-SHB claims.

Sau xác nhận đó mới tạo implementation plans chi tiết và prompt thực thi cho Claude Code.
