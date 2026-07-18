import Link from "next/link";
import React from "react";

import {
  SyntheticDataNotice,
} from "../components/shell/synthetic-data-notice";
import styles from "./landing.module.css";

export const metadata = {
  title: "SHB CreditOps EvidenceGraph — AI hỗ trợ chuẩn bị và phản biện hồ sơ",
  description:
    "Nền tảng evidence-first tổ chức hồ sơ cấp vốn lưu động SME thành một Credit Case Digital Twin có cấu trúc, phiên bản và chuỗi nguồn gốc. AI chuẩn bị và phản biện; con người quyết định.",
};

// The target provenance chain (docs/BANKING_WORKFLOW.md, README) — every
// downstream artifact traces back to a document version and source region.
const PROVENANCE = [
  { doc: "Tài liệu khách hàng", note: "phiên bản bất biến, có nguồn gốc" },
  { doc: "Dữ kiện có vị trí nguồn", note: "trang / vùng và độ tin cậy" },
  { doc: "Phép tính & trích dẫn", note: "deterministic, có căn cứ" },
  { doc: "Kết luận, rủi ro, khoảng trống", note: "được hỗ trợ bằng chứng" },
  { doc: "Phản biện độc lập", note: "maker–checker tách biệt" },
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
// The walking skeleton focuses on stages 2–6; the rest are context / later phase.
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
    mark: "1",
    title: "Mọi kết luận dẫn về bằng chứng",
    body: "Mỗi finding trỏ tới tài liệu, trang và vùng nguồn cụ thể — không có kết luận treo, không dữ kiện bịa.",
  },
  {
    mark: "2",
    title: "Mọi cổng do con người",
    body: "Agent chuẩn bị và phản biện; yêu cầu bổ sung, xử lý ngoại lệ và phê duyệt đều qua người có thẩm quyền.",
  },
  {
    mark: "3",
    title: "Audit append-only",
    body: "Mọi thay đổi trạng thái được ghi bất biến; rerun không làm khoảng trống hay challenge biến mất khỏi lịch sử.",
  },
  {
    mark: "4",
    title: "Maker–checker",
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
          aria-label="SHB CreditOps EvidenceGraph"
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
                SHB CreditOps EvidenceGraph
              </h1>
              <p className={styles.heroTagline}>
                AI hỗ trợ chuẩn bị và phản biện hồ sơ — con người đưa ra quyết
                định.
              </p>
              <p className={styles.heroLede}>
                Nền tảng tổ chức hồ sơ cấp vốn lưu động doanh nghiệp thành một
                bản sao số có cấu trúc, phiên bản và chuỗi nguồn gốc kiểm chứng
                được — thay vì một cuộc hội thoại khó lần lại nguồn.
              </p>
              <div className={styles.heroCtas}>
                <Link
                  className={`button ${styles.ctaPrimary}`}
                  href="/cong-viec"
                >
                  Vào hàng việc của tôi
                </Link>
                <Link
                  className={`button ${styles.ctaSecondary}`}
                  href="/ho-so"
                >
                  Danh sách hồ sơ
                </Link>
              </div>
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
              <p className={styles.eyebrow}>Khái niệm cốt lõi</p>
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
              <p className={styles.eyebrow}>
                Kiến trúc multi-agent có giới hạn thẩm quyền
              </p>
              <h2 className={styles.sectionTitle} id="agents-title">
                Đội ngũ 8 agent chuyên trách
              </h2>
              <p className={styles.sectionLede}>
                Mỗi vai trò là một agent ở tầng ứng dụng với nhiệm vụ, công cụ,
                quyền và schema đầu ra riêng — không phải tám chatbot cùng nói về
                một hồ sơ.
              </p>
            </div>
            <p className={styles.boundaryBanner}>
              Agent không bao giờ phê duyệt hoặc từ chối — con người có thẩm
              quyền quyết định.
            </p>
            <div className={styles.agentGrid}>
              {ROLES.map((role, index) => (
                <article className={styles.roleCard} key={role.name}>
                  <span className={styles.roleIndex}>
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <h3 className={styles.roleName}>{role.name}</h3>
                  <p className={styles.roleDuty}>{role.duty}</p>
                  <span className={styles.roleScope}>{role.scope}</span>
                </article>
              ))}
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
              <p className={styles.eyebrow}>Phủ toàn bộ vòng đời</p>
              <h2 className={styles.sectionTitle} id="stages-title">
                Vòng đời 14 giai đoạn
              </h2>
              <p className={styles.sectionLede}>
                Luồng tín dụng doanh nghiệp khái niệm gồm 14 giai đoạn. Walking
                skeleton hiện tập trung ở giai đoạn 2–6; đây là bối cảnh dự án,
                không phải quy trình chính thức của SHB.
              </p>
            </div>
            <ol className={styles.stageStrip}>
              {STAGES.map((stage, index) => (
                <li
                  className={`${styles.stage} ${
                    stage.inScope ? styles.stageInScope : ""
                  }`}
                  key={stage.label}
                >
                  <span className={styles.stageNum}>{index + 1}</span>
                  <span className={styles.stageLabel}>{stage.label}</span>
                </li>
              ))}
            </ol>
            <div className={styles.stageLegend}>
              <span className={styles.legendItem}>
                <span
                  className={`${styles.legendSwatch} ${styles.legendSwatchScope}`}
                />
                Trong phạm vi walking skeleton (2–6)
              </span>
              <span className={styles.legendItem}>
                <span className={styles.legendSwatch} />
                Bối cảnh / giai đoạn sau
              </span>
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
              <p className={styles.eyebrow}>Vì sao đáng tin</p>
              <h2 className={styles.sectionTitle} id="evidence-title">
                Kiểm chứng được từ gốc
              </h2>
            </div>
            <div className={styles.evidenceGrid}>
              {EVIDENCE.map((item) => (
                <article className={styles.evidenceCard} key={item.title}>
                  <span aria-hidden="true" className={styles.evidenceMark}>
                    {item.mark}
                  </span>
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
              <p className={styles.eyebrow}>Kiến trúc mục tiêu</p>
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
              Đây là topology mục tiêu đã xác nhận, không phải sơ đồ của một môi
              trường đang chạy. Chưa có Supabase, Cloud Run, Vercel hay FPT nào
              được provision và kiểm chứng end-to-end trong workspace này.
            </p>
          </div>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={styles.inner}>
          <SyntheticDataNotice />
          <ul className={styles.footerBoundaries}>
            <li className={styles.footerBoundary}>
              Nội dung synthetic trong dự án không phải chính sách chính thức của
              SHB.
            </li>
            <li className={styles.footerBoundary}>
              Đây không phải hệ thống production và chưa sẵn sàng cho vận hành
              thực tế.
            </li>
            <li className={styles.footerBoundary}>
              Mọi tích hợp với hệ thống ngân hàng đều là mô phỏng có nhãn.
            </li>
            <li className={styles.footerBoundary}>
              Con người có thẩm quyền đưa ra mọi quyết định tín dụng; dự án không
              đại diện cho sự phê duyệt hoặc bảo chứng của SHB.
            </li>
          </ul>
          <p className={styles.footerMeta}>
            SHB CreditOps EvidenceGraph · Walking skeleton evidence-first cho
            chuẩn bị và rà soát hồ sơ vốn lưu động SME.
          </p>
        </div>
      </footer>
    </div>
  );
}
