import Link from "next/link";
import React from "react";

export type CaseSection =
  | "tiep-nhan"
  | "tai-lieu"
  | "doi-chieu"
  | "khoang-trong"
  | "ban-giao"
  | "nhat-ky";

// `tai-lieu` (per-document review) has no top-level tab: it is reached from the
// intake section, so it is intentionally absent from the tab list below.
const CASE_NAV_TABS: readonly {
  section: Exclude<CaseSection, "tai-lieu">;
  label: string;
}[] = [
  { section: "tiep-nhan", label: "Tiếp nhận tài liệu" },
  { section: "doi-chieu", label: "Đối chiếu chứng cứ" },
  { section: "khoang-trong", label: "Khoảng trống chứng cứ" },
  { section: "ban-giao", label: "Bàn giao" },
  { section: "nhat-ky", label: "Nhật ký hồ sơ" },
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
      <Link href="/ho-so">Danh sách hồ sơ</Link>
      <span aria-hidden="true">/</span>
      {CASE_NAV_TABS.map(({ section, label }) =>
        section === current ? (
          <span key={section} aria-current="page">
            {label}
          </span>
        ) : (
          <Link key={section} href={`/ho-so/${caseId}/${section}`}>
            {label}
          </Link>
        ),
      )}
      <span className="case-reference">Mã: {shortReference(caseId)}</span>
    </nav>
  );
}

function shortReference(caseId: string): string {
  return caseId.length > 12 ? `${caseId.slice(0, 8)}…` : caseId;
}
