import type { Metadata } from "next";
import React, { type ReactNode } from "react";

import { AppShell } from "../../components/shell/app-shell";

export const metadata: Metadata = {
  title: "Hàng việc của tôi",
  description:
    "Danh sách việc cần xử lý theo quyền được cấp cho từng hồ sơ tín dụng.",
};

export default function WorkQueueLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
