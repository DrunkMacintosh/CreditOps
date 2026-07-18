import React from "react";

import { CaseList } from "../../components/cases/case-list";
import screen from "../../components/cases/case-screen.module.css";

export default function CasesPage() {
  return (
    <>
      <div className={screen.header}>
        <p className={screen.eyebrow}>Không gian cán bộ tiếp nhận</p>
        <h1 className={screen.title}>Hồ sơ được phân công</h1>
        <p className={screen.lede}>
          Theo dõi hồ sơ tổng hợp dùng cho trình diễn và tiếp nhận tài liệu theo quyền do backend cấp cho từng hồ sơ.
        </p>
      </div>
      <CaseList />
    </>
  );
}
