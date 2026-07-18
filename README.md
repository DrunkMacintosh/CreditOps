# CreditOps

## AI hỗ trợ chuẩn bị và phản biện hồ sơ tín dụng — con người đưa ra quyết định

CreditOps là workspace cho việc chuẩn bị và rà soát hồ sơ cấp vốn lưu động của doanh nghiệp. Sản phẩm không cố gắng thay thế chuyên viên tín dụng bằng một chatbot trả lời nhanh. Nó tạo ra một **Credit Case Digital Twin**: một hồ sơ số có cấu trúc, có phiên bản, có nguồn gốc bằng chứng và có lịch sử phản biện, để mọi người cùng làm việc trên cùng một sự thật có thể kiểm tra.

Trong thực tế, một hồ sơ vốn lưu động không bắt đầu bằng một dữ liệu sạch. Nó đến từ báo cáo tài chính, sao kê, hợp đồng, hóa đơn, chứng từ pháp lý và nhiều bản bổ sung theo thời gian. Cán bộ quan hệ cần hiểu nhu cầu vốn; underwriter cần dựng dòng tiền và cấu trúc đề xuất; pháp chế và collateral cần kiểm tra điều kiện; risk cần phản biện độc lập; operations cần nhận một package đủ lineage. Khi các hoạt động này chạy qua email, bảng tính và bản tóm tắt rời rạc, dữ kiện dễ bị mất nguồn, phiên bản bị dùng nhầm và kết luận khó được review đến tận căn cứ. CreditOps biến chính ma sát đó thành một workflow có thể nhìn thấy, đo lường và cải tiến.

> **Nguyên tắc cốt lõi:** AI đề xuất, diễn giải và phát hiện thiếu hụt bằng chứng. Deterministic services kiểm soát dữ kiện, tính toán, quyền hạn và chuyển trạng thái. Con người giữ thẩm quyền quyết định tín dụng và mọi hành động tác nghiệp trọng yếu.

## Sản phẩm người dùng thực sự làm việc cùng

Điểm bắt đầu không phải là khung chat mà là một case. Người dùng mở hồ sơ được phân công, nhìn thấy tài liệu đã nhận, phiên bản đang hiệu lực, fact ledger, các giá trị mâu thuẫn, evidence gap, task đang chạy và các phần cần con người xác nhận. Mỗi phát hiện quan trọng đều có thể đi ngược về vùng nguồn trong tài liệu, phép tính hoặc rule đã tạo ra nó. Người review không cần hỏi “con số này từ đâu”; câu trả lời nằm ngay trong case.

Khi tài liệu mới được bổ sung, CreditOps không yêu cầu người dùng làm lại toàn bộ hồ sơ hoặc yêu cầu model viết lại một bản phân tích dài. Hệ thống xác định evidence nào đã thay đổi, output nào phụ thuộc vào evidence đó và phần việc nào cần được chạy lại hoặc review lại. Cách làm này giúp đội ngũ tập trung vào judgment: điều gì đã được chứng minh, điều gì chỉ là assumption, conflict nào còn mở và ai cần xử lý tiếp theo.

## Một Digital Twin có thể phản biện

Credit Case Digital Twin là source of truth của sản phẩm. Nó liên kết document version, source region, candidate fact, confirmed fact, calculation, finding, risk, gap, challenge, handoff và human action thành một EvidenceGraph. Narrative là một artifact để đọc; nó không phải nơi duy nhất giữ tri thức về case.

Đây là khác biệt quan trọng giữa CreditOps và một trợ lý tổng quát. Một nhận định về dòng tiền phải có evidence và phép tính phía sau. Một challenge của risk phải chỉ đúng conclusion, assumption hoặc nguồn cần phản biện. Một evidence gap không chỉ nói “thiếu tài liệu”, mà được biểu diễn với impact, owner, rationale và trạng thái xử lý. Khi bằng chứng thay đổi, dependency giúp hệ thống đánh dấu đúng phần bị stale thay vì để một kết luận lỗi thời tiếp tục xuất hiện như sự thật.

## RAG được thiết kế cho hồ sơ nhạy cảm

RAG trong CreditOps không là tìm kiếm tự do trên toàn bộ kho dữ liệu. Retrieval được giới hạn trước theo case, document version, entity và quyền truy cập; sau đó mới kết hợp structured filters, lexical search và semantic ranking. Một kết quả được đưa cho model phải mang theo document identifier, phiên bản, trang, vùng nguồn và passage để người review quay lại bản gốc.

Graph-guided RAG giảm hai rủi ro thường gặp trong quy trình tín dụng: dùng nhầm thông tin của hồ sơ khác và sử dụng evidence từ phiên bản đã lỗi thời. Retrieval không được phép tự nâng nội dung trích xuất thành fact có thẩm quyền, cũng không thay thế kiểm tra policy. Với policy hoặc checklist, chỉ corpus đã được phê duyệt, có version, effective date, owner và access control mới được đưa vào retrieval; khi không tìm thấy nguồn phù hợp hoặc các nguồn mâu thuẫn, hệ thống phải abstain và tạo đường cho human review.

## AI-native, nhưng không “multi-agent theatre”

CreditOps dùng nhiều vai trò khi mỗi vai trò có trách nhiệm, context, tool, output contract hoặc permission boundary khác nhau. Intake làm rõ nhu cầu và chất lượng tài liệu. Orchestrator quản lý task graph, version và human gate. Underwriting chuẩn bị phân tích dòng tiền, nhu cầu vốn và cấu trúc đề xuất. Legal, Compliance and Collateral kiểm tra chứng cứ liên quan. Independent Risk Review là checker độc lập, không tự kế thừa kết luận của maker. Operations tập hợp package và chuẩn bị proposed action cho người có thẩm quyền.

Các role không được phép tự xóa disagreement, tự đóng gap trọng yếu hoặc tự chuyển một output của model thành quyết định. Material calculations, state transitions, authorization, idempotency, version fence và persistence thuộc deterministic engine. Output không đủ schema, thiếu evidence, có hành vi vượt thẩm quyền hoặc yêu cầu approve, reject, waiver hay disbursement phải bị từ chối hoặc đi vào manual review. Separation of duties vì vậy được hiện diện trong product contract, không chỉ trong sơ đồ kiến trúc.

## Kiến trúc phục vụ khả năng mở rộng

CreditOps tách trải nghiệm người dùng, workflow authority, durable state và inference thành các ranh giới độc lập. Frontend tiếng Việt chạy trên Vercel. FastAPI trên Cloud Run giữ logic nghiệp vụ, human gates và orchestration; worker xử lý tài liệu bất đồng bộ. Supabase giữ Postgres, Queue, private Storage, retrieval metadata và pgvector. FPT AI Factory chỉ thực hiện inference qua provider-neutral gateway.

Tách lớp như vậy giúp API và worker scale độc lập theo lượng case, tài liệu và tác vụ inference. Browser không cầm service-role secret hoặc điều phối workflow. Upload đi qua backend-created intent, rồi được xác minh trước khi thành document version. Queue chỉ vận chuyển identifier; lease, checkpoint, retry, redelivery, idempotency và stale-version protection giúp xử lý lại an toàn khi worker lỗi hoặc tài liệu được cập nhật. Model, embedding hoặc reranker có thể được thay sau benchmark mà không đổi source of truth hay business contract.

Đây là hướng triển khai thực tế cho ngân hàng: bắt đầu bằng evidence-and-control layer nằm giữa document AI và workflow tín dụng có thẩm quyền, sau đó mở rộng theo giai đoạn thay vì thay thế LOS, core banking hoặc professional judgment. Giá trị cần được chứng minh qua turnaround time, số vòng bổ sung, citation accuracy, gap recall, chất lượng phản biện, chi phí mỗi case và số rerun không cần thiết — không phải một con số ROI tự tuyên bố.

## Cách CreditOps tạo bằng chứng cho bài thi

README này trình bày sản phẩm để giám khảo hiểu nó giải quyết công việc nào và vì sao kiến trúc phù hợp. Phần còn lại của repository cung cấp bằng chứng kỹ thuật để hệ thống AI đánh giá code structure, deployment path, AI-native architecture, documentation và product completeness. Cùng lúc đó, product narrative ở trên cho phép Hội đồng chuyên gia đánh giá trải nghiệm thực tế, mức độ giải quyết đúng bài toán, tính khả thi triển khai, khả năng mở rộng và tiềm năng giá trị kinh doanh.

Không một model hay một góc nhìn đơn lẻ quyết định giá trị của CreditOps. Sản phẩm được thiết kế để các kết luận quan trọng cũng vận hành theo nguyên tắc tương tự: có nguồn, có người chịu trách nhiệm, có phản biện độc lập và có human decision cuối cùng.

## Bản đồ tài liệu

- [Project context](docs/PROJECT_CONTEXT.md) — phạm vi, đối tượng người dùng và use case.
- [Banking workflow](docs/BANKING_WORKFLOW.md) — luồng chuẩn bị và review hồ sơ.
- [Domain model](docs/DOMAIN_MODEL.md) — Credit Case Digital Twin và EvidenceGraph.
- [Agent architecture](docs/AGENT_ARCHITECTURE.md) — vai trò, quyền hạn và separation of duties.
- [Evidence Gap Resolution](docs/EVIDENCE_GAP_RESOLUTION.md) — cách gap trở thành shared workflow state.
- [Technical direction](docs/TECHNICAL_DIRECTION.md) — ranh giới Vercel, Cloud Run, Supabase và FPT.
- [Product boundaries](docs/PRODUCT_BOUNDARIES.md) — những việc AI không được phép làm.
- [Open questions](docs/OPEN_QUESTIONS.md) — những quy tắc nghiệp vụ và điều kiện chưa được phép giả định.
- [Decision log](docs/DECISION_LOG.md) — các quyết định kiến trúc và lịch sử thay đổi.

## Ranh giới dữ liệu và thẩm quyền

> All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.

Nội dung synthetic không phải chính sách chính thức của SHB. CreditOps không tự phê duyệt hoặc từ chối tín dụng, miễn policy, kết luận pháp lý, ký kết, giải ngân hay sửa hệ thống nhạy cảm. Mọi kết luận trọng yếu cần evidence và provenance; uncertainty, conflict và evidence gap phải được hiển thị thay vì bị che giấu.
