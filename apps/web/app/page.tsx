import React from "react";

const syntheticNotice =
  "All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.";

export default function Home() {
  return (
    <main>
      <section aria-labelledby="intake-title" className="intake-panel">
        <p className="eyebrow">SHB CreditOps EvidenceGraph</p>
        <h1 id="intake-title">Tiếp nhận hồ sơ tín dụng</h1>
        <p className="boundary">
          Không gian chuẩn bị và rà soát hồ sơ vốn lưu động dành cho nhân sự có thẩm
          quyền. Hệ thống không phê duyệt hoặc từ chối cấp tín dụng.
        </p>
        <aside aria-label="Thông báo dữ liệu tổng hợp" className="notice">
          <strong>Phạm vi dữ liệu thử nghiệm</strong>
          <span>{syntheticNotice}</span>
        </aside>
      </section>
    </main>
  );
}
