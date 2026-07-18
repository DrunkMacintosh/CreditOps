import React from "react";

import screen from "../../components/cases/case-screen.module.css";
import { WorkQueue } from "../../components/work-items/work-queue";

export default function WorkQueuePage() {
  return (
    <>
      <div className={screen.header}>
        <p className={screen.eyebrow}>Không gian làm việc</p>
        <h1 className={screen.title}>Hàng việc của tôi</h1>
        <p className={screen.lede}>
          Danh sách việc cần xử lý được suy ra từ quyền do backend cấp cho từng hồ sơ. Mở một việc để đến đúng bước cần thao tác; danh sách chỉ hiển thị, không tự thực hiện hay cấp thêm quyền.
        </p>
      </div>
      <WorkQueue />
    </>
  );
}
