import React from "react";

import { CreateCaseGate } from "../../../components/cases/create-case-gate";
import screen from "../../../components/cases/case-screen.module.css";

export default function CreateCasePage() {
  return (
    <div className={screen.narrow}>
      <div className={screen.header}>
        <p className={screen.eyebrow}>Nhu cầu cấp vốn</p>
        <h1 className={screen.title}>Tạo hồ sơ</h1>
        <p className={screen.lede}>
          Chỉ dùng dữ liệu tổng hợp được tạo cho trình diễn. Không nhập dữ liệu khách hàng thật và không tự suy đoán trường chưa biết.
        </p>
      </div>
      <CreateCaseGate />
    </div>
  );
}
