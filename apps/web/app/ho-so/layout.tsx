import type { Metadata } from "next";
import React, { type ReactNode } from "react";

import { AppShell } from "../../components/shell/app-shell";

export const metadata: Metadata = {
  title: "Hồ sơ được phân công",
  description:
    "Theo dõi hồ sơ tín dụng được phân công và tiếp nhận tài liệu theo quyền.",
};

export default function CaseLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
