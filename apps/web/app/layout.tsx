import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import type { ReactNode } from "react";

import { ScenarioSwitcher } from "../components/scenario/scenario-switcher";
import { ScenarioProvider } from "../lib/fixtures/scenario-context";

import "./globals.css";

// Exposes --font-mono-next so the design-token var(--font-mono) resolves to a
// Vietnamese-capable monospace for currency, references and evidence markers.
const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600"],
  variable: "--font-mono-next",
  display: "swap",
});

// The brand sans was declared as Inter in globals.css but never shipped, so
// visitors silently fell back to the system font. Load it for real, with the
// vietnamese subset so diacritics render from the same face.
const inter = Inter({
  subsets: ["latin", "vietnamese"],
  variable: "--font-sans-next",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://credit-ops-web.vercel.app"),
  title: {
    default: "Tiếp nhận hồ sơ tín dụng | CreditOps EvidenceGraph",
    template: "%s | CreditOps EvidenceGraph",
  },
  description: "Không gian chuẩn bị hồ sơ tín dụng doanh nghiệp có kiểm soát.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html className={`${inter.variable} ${ibmPlexMono.variable}`} lang="vi">
      <body>
        <ScenarioProvider>
          {children}
          <ScenarioSwitcher />
        </ScenarioProvider>
      </body>
    </html>
  );
}
