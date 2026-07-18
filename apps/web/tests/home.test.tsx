import { act, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Home from "../app/page";

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
    screen.getByRole("heading", { name: "CreditOps EvidenceGraph" }),
  ).toBeVisible();
});

it("exposes both hero CTAs with the correct hrefs", () => {
  render(<Home />);
  expect(
    screen.getByRole("link", { name: "Vào hàng việc của tôi" }),
  ).toHaveAttribute("href", "/cong-viec");
  const caseLinks = screen.getAllByRole("link", { name: "Hồ sơ" });
  expect(caseLinks.length).toBeGreaterThan(0);
  for (const link of caseLinks) {
    expect(link).toHaveAttribute("href", "/ho-so");
  }
});

it("shows a truthful synthetic-data label on the landing page", () => {
  render(<Home />);
  expect(
    screen.getByText(
      /Dữ liệu khách hàng, chính sách, tài liệu và phản hồi hệ thống ngân hàng trong nền tảng là dữ liệu tổng hợp/,
    ),
  ).toBeVisible();
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
      /Agent không bao giờ phê duyệt hoặc từ chối; con người có thẩm quyền quyết định/,
    ),
  ).toBeVisible();
});

it("makes no false SHB-approval or production-readiness claim", () => {
  render(<Home />);
  const text = document.body.textContent ?? "";
  expect(text).not.toContain("được SHB phê duyệt");
  expect(text).not.toContain("production-ready");
});

describe("demo session CTA", () => {
  const DEMO_CTA = "Trải nghiệm demo (dữ liệu tổng hợp)";
  const originalLocation = window.location;
  let assignMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    assignMock = vi.fn();
    // jsdom's window.location.assign is non-configurable; replace the whole
    // location object for the duration of this suite instead of spying on it.
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, assign: assignMock },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps the existing CTAs alongside the new primary demo CTA", () => {
    render(<Home />);
    expect(screen.getByRole("button", { name: DEMO_CTA })).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Vào hàng việc của tôi" }),
    ).toHaveAttribute("href", "/cong-viec");
    for (const link of screen.getAllByRole("link", { name: "Hồ sơ" })) {
      expect(link).toHaveAttribute("href", "/ho-so");
    }
  });

  it("mints a demo session and redirects into the working app with its caseId", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ caseId: "case-42" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Home />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: DEMO_CTA }));
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/demo-session",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    expect(assignMock).toHaveBeenCalledWith("/ho-so/case-42/tiep-nhan");
  });

  it("shows an honest error and never redirects when the demo session cannot be minted", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: "UPSTREAM_UNAVAILABLE" }), {
        status: 502,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Home />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: DEMO_CTA }));
    });

    expect(
      await screen.findByText(
        "Không thể khởi tạo phiên demo lúc này. Vui lòng thử lại sau ít phút.",
      ),
    ).toBeVisible();
    expect(assignMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: DEMO_CTA })).not.toBeDisabled();
  });
});
