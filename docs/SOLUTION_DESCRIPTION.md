# SOLUTION DESCRIPTION — CREDITOPT / SHB CREDITOPS EVIDENCEGRAPH

## Bài toán và cơ hội

DNNVV chiếm khoảng 98% trong gần 930.000 doanh nghiệp đang hoạt động tại Việt Nam ([MPI, 2024](https://mpi.gov.vn/portal/Pages/2024-8-28/Bo-truong-Nguyen-Chi-Dung-doanh-nghiep-nho-va-vua-lyv5l6.aspx)). World Bank Enterprise Survey 2023 ghi nhận 21,2% doanh nghiệp xem tiếp cận tài chính là trở ngại lớn nhất; ở doanh nghiệp vừa là 30,7% ([World Bank, 2023](https://www.enterprisesurveys.org/content/dam/enterprisesurveys/documents/country/Viet-Nam-2023.pdf)). CreditOpt tập trung vào ma sát có thể đo: chi phí tìm, kiểm tra và chứng minh thông tin hồ sơ.

Một nhu cầu vốn lưu động gồm nhiều tài liệu và phiên bản. Xử lý phân mảnh làm tăng công sức đối chiếu, nguy cơ bỏ sót, dùng nhầm version và dựng lại audit trail. Chatbot không đủ để quản lý nguồn gốc, phụ thuộc, separation of duties và thẩm quyền.

## Giải pháp

CreditOpt là hệ thống multi-agent hỗ trợ chuẩn bị và rà soát hồ sơ vốn lưu động SME/KHDN, xoay quanh **Credit Case Digital Twin** có cấu trúc, version và provenance — không dùng lịch sử chat làm source of truth.

Luồng mục tiêu: upload → candidate facts kèm vùng nguồn → Intake Officer xác nhận → Underwriting và Legal/Compliance/Collateral phân tích → Risk challenge độc lập → Operations chuẩn bị package → con người quyết định. Evidence mới làm output phụ thuộc thành stale để chạy lại có mục tiêu.

Nguyên tắc: **LLM đề xuất và diễn giải; deterministic engine tính toán, kiểm tra quyền và chuyển trạng thái; con người quyết định tín dụng.**

## Độ hữu dụng và tác động

CreditOpt giúp Intake phát hiện thiếu/mâu thuẫn; Underwriter dùng phép tính nhất quán; Legal/Risk review đúng nguồn/version; Operations nhận package có lineage; Audit có dấu vết sinh ra trong workflow.

Tác động đo bằng turnaround time, số vòng bổ sung, manual touches, gap/conflict precision–recall, citation precision, calculation exactness và cost/latency/token. Gate bypass, cross-case leakage và unauthorized mutation phải bằng 0; không dùng ROI chưa benchmark.

## Điểm đổi mới

1. **EvidenceGraph + Digital Twin:** nối tài liệu, facts, calculations, findings, risks, challenges và human actions thành lineage có thể kiểm tra và replay.
2. **Human-confirmed facts:** model không tự nâng extraction xác suất thành dữ kiện có thẩm quyền.
3. **Graph-guided RAG:** graph giới hạn case/version/entity; retrieval tìm passage rồi quay về tài liệu gốc kiểm tra citation, giảm stale và cross-case hit.
4. **Deterministic-tools-first:** số học, rules, authorization và state transitions chạy bằng code.
5. **Maker–checker contract:** maker/checker có context, output và execution riêng; challenge không tự bị xóa.
6. **Structured handoff:** agent sau kế thừa claims, evidence, assumptions và calculations, không lưu hidden chain-of-thought.

## Khả năng scale và market fit

Thị trường có nền tảng commercial lending end-to-end như [nCino](https://www.ncino.com/solutions/commercial-lending) và document processing như [ABBYY](https://www.abbyy.com/document-ai/). CreditOpt không thay LOS/core banking; định vị cần kiểm chứng là **evidence-and-control layer** nằm giữa document AI và workflow tín dụng có thẩm quyền.

Kiến trúc mục tiêu: **Vercel → Cloud Run → Supabase → FPT AI Factory**. API và workers scale độc lập; queue, checkpoint, idempotency và version fence hỗ trợ retry. Capability routing cho phép thay model sau benchmark. Protocol evidence/task/artifact/audit hỗ trợ mở rộng từ stages 2–6 sang 14 giai đoạn. Đây là thiết kế, chưa phải kết quả load-test hay production.

## An toàn, trạng thái và giới hạn

AI không được phê duyệt/từ chối tín dụng, miễn chính sách, kết luận pháp lý, ký, giải ngân hoặc sửa hệ thống nhạy cảm. Material claim phải có nguồn; uncertainty và gaps phải hiển thị. Tài liệu upload là untrusted input.

Repository có local walking skeleton cho stages 2–6: UI tiếng Việt, API/domain, processors, calculators, model gateway và test/security foundations. Worker orchestration, handoff, revision, HumanCreditDecision và cloud deployment chưa hoàn tất. FPT routes fail closed khi thiếu benchmark-pass evidence. Chưa có quy trình/chính sách SHB chính thức; dự án chưa production-ready.

> All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.
