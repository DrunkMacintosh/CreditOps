import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Tiếp nhận hồ sơ tín dụng | SHB CreditOps EvidenceGraph",
  description: "Không gian chuẩn bị hồ sơ tín dụng doanh nghiệp có kiểm soát.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="vi">
      <body>{children}</body>
    </html>
  );
}
