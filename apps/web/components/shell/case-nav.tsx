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
  | "ban-giao"
  | "nhat-ky";

// `tai-lieu` (per-document review) has no tab of its own: it is reached from the
// intake section, so it is intentionally absent from the groups below. When it is
// the current section no tab is marked, which mirrors the earlier behaviour.
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
  return (
    <nav aria-label="Điều hướng hồ sơ" className="case-nav">
      <div className="case-nav-top">
        <Link className="case-nav-back" href="/ho-so">
          <span aria-hidden="true">←</span> Danh sách hồ sơ
        </Link>
        <span className="case-reference">Mã: {shortReference(caseId)}</span>
      </div>
      <div className="case-nav-groups">
        {CASE_NAV_GROUPS.map((group) => (
          <div className="case-nav-group" key={group.label}>
            <span className="case-nav-group-label">{group.label}</span>
            <ul className="case-nav-tabs">
              {group.tabs.map(({ section, label }) =>
                section === current ? (
                  <li key={section}>
                    <span aria-current="page" className="case-nav-tab is-current">
                      {label}
                    </span>
                  </li>
                ) : (
                  <li key={section}>
                    <Link className="case-nav-tab" href={`/ho-so/${caseId}/${section}`}>
                      {label}
                    </Link>
                  </li>
                ),
              )}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}

function shortReference(caseId: string): string {
  return caseId.length > 12 ? `${caseId.slice(0, 8)}…` : caseId;
}
