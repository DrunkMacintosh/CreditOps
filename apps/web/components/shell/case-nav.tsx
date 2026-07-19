import Link from "next/link";
import React from "react";

export type CaseSection =
  | "tiep-nhan"
  | "tai-lieu"
  | "doi-chieu"
  | "khoang-trong"
  | "quy-trinh"
  | "tham-dinh"
  | "phap-che"
  | "rui-ro"
  | "tong-hop"
  | "thong-bao"
  | "hop-dong"
  | "bao-dam"
  | "dieu-kien-giai-ngan"
  | "giai-ngan"
  | "giam-sat"
  | "thu-no"
  | "tat-toan-xu-ly-no"
  | "ban-giao"
  | "nhat-ky";

// `tai-lieu` (per-document review) has no tab of its own: it is reached from the
// intake section, so it is intentionally absent from the groups below. When it is
// the current section no phase is marked, which mirrors the earlier behaviour.
type NavTab = { section: Exclude<CaseSection, "tai-lieu">; label: string };

const CASE_NAV_GROUPS: readonly { label: string; tabs: readonly NavTab[] }[] = [
  {
    label: "Chuẩn bị",
    tabs: [
      { section: "tiep-nhan", label: "Tiếp nhận tài liệu" },
      { section: "doi-chieu", label: "Đối chiếu chứng cứ" },
      { section: "khoang-trong", label: "Khoảng trống chứng cứ" },
    ],
  },
  {
    label: "Đánh giá",
    tabs: [
      { section: "quy-trinh", label: "Quy trình" },
      { section: "tham-dinh", label: "Thẩm định" },
      { section: "phap-che", label: "Pháp chế" },
      { section: "rui-ro", label: "Rủi ro" },
      { section: "tong-hop", label: "Tổng hợp" },
    ],
  },
  {
    label: "Hợp đồng và giải ngân",
    tabs: [
      { section: "thong-bao", label: "Thông báo tín dụng" },
      { section: "hop-dong", label: "Hồ sơ hợp đồng" },
      { section: "bao-dam", label: "Hoàn thiện bảo đảm" },
      { section: "dieu-kien-giai-ngan", label: "Điều kiện giải ngân" },
      { section: "giai-ngan", label: "Giải ngân vốn vay" },
    ],
  },
  {
    label: "Sau cấp tín dụng",
    tabs: [
      { section: "giam-sat", label: "Giám sát sau cấp tín dụng" },
      { section: "thu-no", label: "Thu nợ gốc, lãi và phí" },
      { section: "tat-toan-xu-ly-no", label: "Tất toán và xử lý nợ" },
    ],
  },
  {
    label: "Hoàn tất",
    tabs: [
      { section: "ban-giao", label: "Bàn giao" },
      { section: "nhat-ky", label: "Nhật ký hồ sơ" },
    ],
  },
];

export function CaseNav({
  caseId,
  current = "tiep-nhan",
}: {
  caseId: string;
  current?: CaseSection;
}) {
  const activeGroupIndex = CASE_NAV_GROUPS.findIndex((group) =>
    group.tabs.some((tab) => tab.section === current),
  );

  return (
    <nav aria-label="Điều hướng hồ sơ" className="case-nav">
      <div className="case-nav-top">
        <Link className="case-nav-back" href="/ho-so">
          <span aria-hidden="true">←</span> Danh sách hồ sơ
        </Link>
        <span className="case-reference">Mã: {shortReference(caseId)}</span>
      </div>
      <ol className="case-timeline">
        {CASE_NAV_GROUPS.map((group, index) => {
          const state =
            activeGroupIndex === -1 || index > activeGroupIndex
              ? "is-ahead"
              : index === activeGroupIndex
                ? "is-active"
                : "is-past";
          const currentTab = group.tabs.find((tab) => tab.section === current);
          const phaseIndex = String(index + 1).padStart(2, "0");
          return (
            <li className={`case-phase ${state}`} key={group.label}>
              <Link
                className="case-phase-head"
                href={`/ho-so/${caseId}/${group.tabs[0].section}`}
              >
                <span aria-hidden="true" className="case-phase-node" />
                <span className="case-phase-name">
                  <span aria-hidden="true" className="case-phase-index">
                    {phaseIndex}
                  </span>
                  <span className="case-phase-label">{group.label}</span>
                </span>
              </Link>
              {currentTab ? (
                <span className="case-phase-current-step">{currentTab.label}</span>
              ) : null}
              <div className="case-phase-popover">
                <div className="case-phase-popover-card">
                  <span aria-hidden="true" className="case-phase-popover-title">
                    {phaseIndex} · {group.label}
                  </span>
                  <ul className="case-phase-steps">
                    {group.tabs.map(({ section, label }) =>
                      section === current ? (
                        <li key={section}>
                          <span aria-current="page" className="case-phase-step is-current">
                            {label}
                          </span>
                        </li>
                      ) : (
                        <li key={section}>
                          <Link
                            className="case-phase-step"
                            href={`/ho-so/${caseId}/${section}`}
                          >
                            {label}
                          </Link>
                        </li>
                      ),
                    )}
                  </ul>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function shortReference(caseId: string): string {
  return caseId.length > 12 ? `${caseId.slice(0, 8)}…` : caseId;
}
