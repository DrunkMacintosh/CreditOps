import Link from "next/link";
import React from "react";

import { DemoCta } from "../components/landing/demo-cta";
import { MobileNav } from "../components/shell/mobile-nav";

import styles from "./landing.module.css";

export const metadata = {
  title: "CreditOps EvidenceGraph | AI hỗ trợ chuẩn bị và phản biện hồ sơ",
  description:
    "Nền tảng evidence-first tổ chức hồ sơ cấp vốn lưu động SME thành một Credit Case Digital Twin có cấu trúc, phiên bản và chuỗi nguồn gốc. AI chuẩn bị và phản biện; con người quyết định.",
  openGraph: {
    title: "CreditOps EvidenceGraph",
    description:
      "AI hỗ trợ chuẩn bị và phản biện hồ sơ tín dụng. Con người đưa ra quyết định.",
    type: "website",
    locale: "vi_VN",
    siteName: "CreditOps EvidenceGraph",
  },
};

// The target provenance chain (docs/BANKING_WORKFLOW.md, README): every
// downstream artifact traces back to a document version and source region.
const PROVENANCE = [
  { doc: "Tài liệu khách hàng", note: "phiên bản bất biến, có nguồn gốc" },
  { doc: "Dữ kiện có vị trí nguồn", note: "trang / vùng và độ tin cậy" },
  { doc: "Phép tính & trích dẫn", note: "deterministic, có căn cứ" },
  { doc: "Kết luận, rủi ro, khoảng trống", note: "được hỗ trợ bằng chứng" },
  { doc: "Phản biện độc lập", note: "maker-checker tách biệt" },
  { doc: "Quyết định của con người", note: "thẩm quyền cuối cùng" },
];

// Eight specialist roles. Roles operate as bounded application-layer agents:
// they prepare and challenge, they never approve or reject (README boundary).
const ROLES = [
  {
    name: "Điều phối hồ sơ",
    duty: "Đọc trạng thái hồ sơ, xác định việc sẵn sàng / chờ / lỗi thời, điều phối và dừng ở cổng con người.",
    scope: "Trong phạm vi",
  },
  {
    name: "Quan hệ & Tiếp nhận",
    duty: "Cấu trúc nhu cầu vốn, gắn tài liệu, phát hiện thiếu / trùng / xung đột ban đầu.",
    scope: "Trong phạm vi",
  },
  {
    name: "Thẩm định tín dụng",
    duty: "Chuẩn bị phân tích kinh doanh, tài chính, dòng tiền, vốn lưu động và cấu trúc đề xuất.",
    scope: "Trong phạm vi",
  },
  {
    name: "Pháp chế & TSBĐ",
    duty: "Rà soát tư cách, thẩm quyền, sở hữu, chính sách và hồ sơ tài sản bảo đảm.",
    scope: "Trong phạm vi",
  },
  {
    name: "Rà soát rủi ro độc lập",
    duty: "Phản biện maker, tìm thiếu sót, giả định yếu, mitigant chưa đủ và khoảng trống chưa đóng.",
    scope: "Trong phạm vi",
  },
  {
    name: "Tác nghiệp tín dụng",
    duty: "Kiểm tra hồ sơ, gom chuỗi nguồn gốc và soạn tờ trình / đề xuất nháp cho người xem xét.",
    scope: "Trong phạm vi",
  },
  {
    name: "Giám sát sau cấp",
    duty: "Theo dõi điều kiện sau cấp vốn và dấu hiệu cần rà soát lại hồ sơ.",
    scope: "Giai đoạn sau",
  },
  {
    name: "Thu nợ & Xử lý",
    duty: "Chuẩn bị thông tin cho xử lý nợ và thu hồi khi phát sinh, dưới quyền con người.",
    scope: "Giai đoạn sau",
  },
];

// The complete conceptual corporate-credit lifecycle (docs/BANKING_WORKFLOW.md).
// The platform focuses on stages 2-6; the rest are context / later phase.
const STAGES = [
  { label: "Nhận diện & tiếp cận khách hàng", inScope: false },
  { label: "Xác định nhu cầu vốn", inScope: true },
  { label: "Thu thập & kiểm tra tài liệu", inScope: true },
  { label: "Thẩm định khách hàng & tín dụng", inScope: true },
  { label: "Chuẩn bị cấu trúc cấp tín dụng đề xuất", inScope: true },
  { label: "Rà soát độc lập & phê duyệt của con người", inScope: true },
  { label: "Thông báo cấp tín dụng", inScope: false },
  { label: "Chuẩn bị & ký hợp đồng", inScope: false },
  { label: "Hoàn tất biện pháp bảo đảm", inScope: false },
  { label: "Kiểm tra điều kiện giải ngân", inScope: false },
  { label: "Giải ngân", inScope: false },
  { label: "Giám sát sau cấp tín dụng", inScope: false },
  { label: "Thu gốc, lãi & phí", inScope: false },
  { label: "Tất toán hoặc xử lý thu hồi nợ", inScope: false },
];

const EVIDENCE = [
  {
    title: "Mọi kết luận dẫn về bằng chứng",
    body: "Mỗi finding trỏ tới tài liệu, trang và vùng nguồn cụ thể: không có kết luận treo, không dữ kiện bịa.",
  },
  {
    title: "Mọi cổng do con người",
    body: "Agent chuẩn bị và phản biện; yêu cầu bổ sung, xử lý ngoại lệ và phê duyệt đều qua người có thẩm quyền.",
  },
  {
    title: "Audit append-only",
    body: "Mọi thay đổi trạng thái được ghi bất biến; rerun không làm khoảng trống hay challenge biến mất khỏi lịch sử.",
  },
  {
    title: "Maker-checker",
    body: "Thẩm định (maker) và rà soát rủi ro độc lập (checker) tách biệt; không ai vừa lập vừa tự thông qua.",
  },
];

const ARCHITECTURE = [
  {
    tier: "Trình bày",
    name: "Vercel",
    body: "Next.js tiếng Việt cho cán bộ tiếp nhận và reviewer; chuyển tài liệu trực tiếp lên Storage.",
  },
  {
    tier: "Điều phối",
    name: "Cloud Run · FastAPI",
    body: "API riêng tư, orchestration và worker; điểm thực thi authorization, human gate và audit.",
  },
  {
    tier: "Trạng thái bền",
    name: "Supabase",
    body: "PostgreSQL + RLS + pgvector + Queues + Storage: nguồn sự thật, hàng đợi và đối tượng riêng tư.",
  },
  {
    tier: "Suy luận",
    name: "FPT AI Factory",
    body: "Chỉ thực hiện inference; output có schema, không trở thành sự thật, quyền hạn hay phê duyệt.",
  },
];

export default function Home() {
  return (
    <div className={styles.page}>
      <a className="skip-link" href="#noi-dung-chinh">
        Chuyển đến nội dung chính
      </a>

      <header className={styles.topbar}>
        <Link
          aria-label="CreditOps EvidenceGraph"
          className="brand"
          href="/"
        >
          <span aria-hidden="true" className="brand-mark">
            CE
          </span>
          <span>
            <strong>CreditOps</strong>
            <small>EvidenceGraph</small>
          </span>
        </Link>
        <nav aria-label="Điều hướng chính" className={styles.topnav}>
          <Link href="/cong-viec">Hàng việc của tôi</Link>
          <Link href="/ho-so">Hồ sơ</Link>
        </nav>
        <MobileNav>
          <Link href="/cong-viec">Hàng việc của tôi</Link>
          <Link href="/ho-so">Hồ sơ</Link>
        </MobileNav>
      </header>

      <main className={styles.main} id="noi-dung-chinh" tabIndex={-1}>
        {/* Hero */}
        <section aria-labelledby="hero-title" className={styles.hero}>
          <div className={styles.heroInner}>
            <div>
              <p className={styles.heroEyebrow}>
                Evidence-first · Vốn lưu động SME
              </p>
              <h1 className={styles.heroTitle} id="hero-title">
                CreditOps EvidenceGraph
              </h1>
              <p className={styles.heroTagline}>
                AI hỗ trợ chuẩn bị và phản biện hồ&nbsp;sơ.{" "}
                <em className={styles.heroTaglineHuman}>
                  Con người đưa ra quyết định.
                </em>
              </p>
              <DemoCta />
              {/* Synthetic-data disclosure for the demo entry point; kept in
                  the hero deliberately so the honest-labeling boundary is
                  visible before anyone clicks. */}
              <p className={styles.heroNote}>
                Phiên demo tạo một hồ sơ tín dụng tổng hợp mới và đưa bạn thẳng
                vào bước tiếp nhận tài liệu; dữ liệu hoàn toàn tổng hợp, không
                phải khách hàng thật.
              </p>
            </div>

            <aside
              aria-label="Chuỗi nguồn gốc bằng chứng"
              className={styles.provCard}
            >
              <p className={styles.provLabel}>Chuỗi nguồn gốc</p>
              <ol className={styles.provList}>
                {PROVENANCE.map((step, index) => (
                  <li className={styles.provStep} key={step.doc}>
                    <span aria-hidden="true" className={styles.provDot}>
                      {index + 1}
                    </span>
                    <span className={styles.provText}>
                      {step.doc}
                      <span>{step.note}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </aside>
          </div>
        </section>

        {/* Credit Case Digital Twin */}
        <section
          aria-labelledby="twin-title"
          className={styles.section}
        >
          <div className={styles.inner}>
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionTitle} id="twin-title">
                Credit Case Digital Twin
              </h2>
            </div>
            <div className={styles.twinGrid}>
              <div className={styles.twinCopy}>
                <p>
                  Mỗi hồ sơ được tổ chức thành một bản sao số{" "}
                  <strong>có cấu trúc, có phiên bản và có chuỗi nguồn gốc</strong>
                  : tài liệu nào tạo ra dữ kiện nào, phép tính nào dùng dữ kiện
                  đó, kết luận nào dựa trên bằng chứng nào và ai đã rà soát kết
                  luận ấy.
                </p>
                <p>
                  Khi một phiên bản tài liệu thay đổi, các kết luận phụ thuộc
                  được đánh dấu lỗi thời thay vì bị ghi đè im lặng. Trạng thái,
                  bằng chứng và quyền hạn nằm trong artifact có thể kiểm chứng.
                </p>
                <p>
                  <strong>Hội thoại không phải nguồn sự thật.</strong> Trọng tâm
                  chuyển từ “AI trả lời gì?” sang “artifact nào đang ở trạng thái
                  nào, dựa trên bằng chứng nào và cần ai xử lý tiếp?”.
                </p>
              </div>
              <ul className={styles.principleList}>
                <li className={styles.principle}>
                  <strong>Có cấu trúc</strong>
                  <span>Tách fact, phép tính, kết luận và đề xuất.</span>
                </li>
                <li className={styles.principle}>
                  <strong>Có phiên bản</strong>
                  <span>Tài liệu và dữ kiện bất biến theo phiên bản.</span>
                </li>
                <li className={styles.principle}>
                  <strong>Truy vết được</strong>
                  <span>Mỗi kết luận lần về đúng nguồn gốc.</span>
                </li>
                <li className={styles.principle}>
                  <strong>Không stale ngầm</strong>
                  <span>Bằng chứng đổi thì downstream được đánh dấu.</span>
                </li>
              </ul>
            </div>
          </div>
        </section>

        {/* Eight agents */}
        <section
          aria-labelledby="agents-title"
          className={`${styles.section} ${styles.sectionAlt}`}
        >
          <div className={styles.inner}>
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionTitle} id="agents-title">
                Đội ngũ 8 agent chuyên trách
              </h2>
              <p className={styles.sectionLede}>
                Mỗi vai trò là một agent ở tầng ứng dụng với nhiệm vụ, công cụ,
                quyền và schema đầu ra riêng, không phải tám chatbot cùng nói về
                một hồ sơ.
              </p>
            </div>
            <p className={styles.boundaryBanner}>
              Agent không bao giờ phê duyệt hoặc từ chối; con người có thẩm
              quyền quyết định.
            </p>
            <div className={styles.agentGroups}>
              <div className={styles.agentGroup}>
                <p className={styles.agentGroupLabel}>Trong phạm vi hiện tại</p>
                <ul className={styles.agentList}>
                  {ROLES.filter((role) => role.scope === "Trong phạm vi").map(
                    (role) => (
                      <li className={styles.agentRow} key={role.name}>
                        <h3 className={styles.roleName}>{role.name}</h3>
                        <p className={styles.roleDuty}>{role.duty}</p>
                      </li>
                    ),
                  )}
                </ul>
              </div>
              <div className={`${styles.agentGroup} ${styles.agentGroupLater}`}>
                <p className={styles.agentGroupLabel}>Giai đoạn sau</p>
                <ul className={styles.agentList}>
                  {ROLES.filter((role) => role.scope === "Giai đoạn sau").map(
                    (role) => (
                      <li className={styles.agentRow} key={role.name}>
                        <h3 className={styles.roleName}>{role.name}</h3>
                        <p className={styles.roleDuty}>{role.duty}</p>
                      </li>
                    ),
                  )}
                </ul>
              </div>
            </div>
          </div>
        </section>

        {/* 14-stage lifecycle */}
        <section
          aria-labelledby="stages-title"
          className={styles.section}
        >
          <div className={styles.inner}>
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionTitle} id="stages-title">
                Vòng đời 14 giai đoạn
              </h2>
              <p className={styles.sectionLede}>
                Luồng tín dụng doanh nghiệp gồm 14 giai đoạn; nền tảng hiện tập
                trung vào giai đoạn 2-6. Đây là bối cảnh dự án, không phải quy
                trình chính thức của một ngân hàng cụ thể.
              </p>
            </div>
            <div className={styles.stagePhases}>
              <div className={styles.stageFocus}>
                <p className={styles.stagePhaseLabel}>
                  Trọng tâm nền tảng · giai đoạn 2-6
                </p>
                <ol className={styles.stageFocusList} start={2}>
                  {STAGES.filter((stage) => stage.inScope).map(
                    (stage, index) => (
                      <li className={styles.stageFocusItem} key={stage.label}>
                        <span className={styles.stageNum}>{index + 2}</span>
                        <span className={styles.stageLabel}>{stage.label}</span>
                      </li>
                    ),
                  )}
                </ol>
              </div>
              <div className={styles.stageContext}>
                <div className={styles.stageContextGroup}>
                  <p className={styles.stagePhaseLabel}>Trước tiếp nhận</p>
                  <ol className={styles.stageContextList}>
                    <li className={styles.stageContextItem}>
                      <span className={styles.stageContextNum}>1</span>
                      {STAGES[0].label}
                    </li>
                  </ol>
                </div>
                <div className={styles.stageContextGroup}>
                  <p className={styles.stagePhaseLabel}>Giai đoạn sau</p>
                  <ol className={styles.stageContextList} start={7}>
                    {STAGES.slice(6).map((stage, index) => (
                      <li className={styles.stageContextItem} key={stage.label}>
                        <span className={styles.stageContextNum}>
                          {index + 7}
                        </span>
                        {stage.label}
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Verifiable from source */}
        <section
          aria-labelledby="evidence-title"
          className={`${styles.section} ${styles.sectionAlt}`}
        >
          <div className={styles.inner}>
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionTitle} id="evidence-title">
                Kiểm chứng được từ gốc
              </h2>
            </div>
            <div className={styles.evidenceGrid}>
              {EVIDENCE.map((item) => (
                <article className={styles.evidenceItem} key={item.title}>
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* Architecture */}
        <section
          aria-labelledby="arch-title"
          className={styles.section}
        >
          <div className={styles.inner}>
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionTitle} id="arch-title">
                Kiến trúc
              </h2>
            </div>
            <ol className={styles.archFlow}>
              {ARCHITECTURE.map((node) => (
                <li className={styles.archNode} key={node.name}>
                  <p className={styles.archTier}>{node.tier}</p>
                  <h3>{node.name}</h3>
                  <p>{node.body}</p>
                </li>
              ))}
            </ol>
            <p className={styles.archNote}>
              Kiến trúc này đã được triển khai trên Vercel (web), Cloud Run (API
              và worker) và Supabase (dữ liệu, hàng đợi, lưu trữ). FPT AI Factory
              cung cấp suy luận; output của model có schema, được kích hoạt theo
              cổng benchmark và không trở thành sự thật hay phê duyệt nếu chưa
              qua kiểm chứng và cổng con người.
            </p>
          </div>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={styles.inner}>
          <aside
            aria-label="Thông báo dữ liệu tổng hợp"
            className="synthetic-notice"
          >
            <span aria-hidden="true" className="notice-mark">
              S
            </span>
            <div>
              <strong>Dữ liệu tổng hợp</strong>
              <p>
                Dữ liệu khách hàng, chính sách, tài liệu và phản hồi hệ thống
                ngân hàng trong nền tảng là dữ liệu tổng hợp (synthetic).
              </p>
            </div>
          </aside>
          <ul className={styles.footerBoundaries}>
            <li className={styles.footerBoundary}>
              Con người có thẩm quyền đưa ra mọi quyết định tín dụng; agent chuẩn
              bị và phản biện, không phê duyệt hay từ chối.
            </li>
            <li className={styles.footerBoundary}>
              Mọi tích hợp với hệ thống ngân hàng đều là mô phỏng có nhãn.
            </li>
            <li className={styles.footerBoundary}>
              Nền tảng không đại diện cho sự phê duyệt hoặc bảo chứng của bất kỳ
              ngân hàng nào.
            </li>
          </ul>
          <p className={styles.footerMeta}>
            CreditOps EvidenceGraph · Nền tảng evidence-first cho chuẩn bị và rà
            soát hồ sơ vốn lưu động SME.
          </p>
        </div>
      </footer>
    </div>
  );
}
