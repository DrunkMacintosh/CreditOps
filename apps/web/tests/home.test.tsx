import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import React from "react";

import Home from "../app/page";
import {
  SYNTHETIC_DATA_NOTICE,
  SYNTHETIC_DATA_NOTICE_VI,
} from "../components/shell/synthetic-data-notice";

// The eight specialist roles must all be named on the landing page
// (task brief; README multi-agent architecture with authority boundary).
const ROLE_NAMES = [
  "Điều phối hồ sơ",
  "Quan hệ & Tiếp nhận",
  "Thẩm định tín dụng",
  "Pháp chế & TSBĐ",
  "Rà soát rủi ro độc lập",
  "Tác nghiệp tín dụng",
  "Giám sát sau cấp",
  "Thu nợ & Xử lý",
];

it("renders the hero heading with the product name", () => {
  render(<Home />);
  expect(
    screen.getByRole("heading", { name: "SHB CreditOps EvidenceGraph" }),
  ).toBeVisible();
});

it("exposes both hero CTAs with the correct hrefs", () => {
  render(<Home />);
  expect(
    screen.getByRole("link", { name: "Vào hàng việc của tôi" }),
  ).toHaveAttribute("href", "/cong-viec");
  expect(
    screen.getByRole("link", { name: "Danh sách hồ sơ" }),
  ).toHaveAttribute("href", "/ho-so");
});

it("shows the canonical synthetic notice verbatim in both languages", () => {
  render(<Home />);
  expect(screen.getByText(SYNTHETIC_DATA_NOTICE_VI)).toBeVisible();
  expect(screen.getByText(SYNTHETIC_DATA_NOTICE)).toBeVisible();
});

it("names all eight specialist agent roles", () => {
  render(<Home />);
  for (const name of ROLE_NAMES) {
    expect(screen.getByRole("heading", { name })).toBeVisible();
  }
});

it("states the human-authority boundary", () => {
  render(<Home />);
  expect(
    screen.getByText(
      /Agent không bao giờ phê duyệt hoặc từ chối — con người có thẩm quyền quyết định/,
    ),
  ).toBeVisible();
});

it("makes no false SHB-approval or production-readiness claim", () => {
  render(<Home />);
  const text = document.body.textContent ?? "";
  expect(text).not.toContain("được SHB phê duyệt");
  expect(text).not.toContain("production-ready");
});
