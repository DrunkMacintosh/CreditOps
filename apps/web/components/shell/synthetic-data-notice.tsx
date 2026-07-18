import React from "react";

// Canonical synthetic-data notice (AGENTS.md, Non-negotiable boundaries).
// shared/synthetic-notice.json is the single source of truth; tests assert
// these constants equal it exactly. Change only via a reviewed governance
// decision recorded in docs/DECISION_LOG.md.
export const SYNTHETIC_DATA_NOTICE =
  "All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.";

export const SYNTHETIC_DATA_NOTICE_VI =
  "Toàn bộ dữ liệu khách hàng, chính sách, tài liệu và phản hồi hệ thống ngân hàng trong dự án này là dữ liệu tổng hợp, được tạo riêng cho mục đích trình diễn.";

export function SyntheticDataNotice() {
  return (
    <aside aria-label="Thông báo dữ liệu tổng hợp" className="synthetic-notice">
      <span aria-hidden="true" className="notice-mark">S</span>
      <div>
        <strong>Dữ liệu tổng hợp dùng cho trình diễn</strong>
        <p>{SYNTHETIC_DATA_NOTICE_VI}</p>
        <p lang="en">{SYNTHETIC_DATA_NOTICE}</p>
      </div>
    </aside>
  );
}
