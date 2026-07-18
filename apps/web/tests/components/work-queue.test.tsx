import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { WorkQueue } from "../../components/work-items/work-queue";
import { parseWorkItemList } from "../../lib/api/work-items";

// Build the list the way the client really would: raw camelCase payload run
// through the defensive parser, so `supported` (and thus fail-closed rendering)
// is derived from the enums exactly as in production — never hand-set.
function list(rawItems: unknown[]) {
  return parseWorkItemList({ items: rawItems });
}

const blockingItem = {
  caseId: "case-block",
  caseVersion: 3,
  kind: "MANUAL_REVIEW",
  titleVi: "Cần rà soát thủ công",
  reasonVi: "Tự động hóa dừng, chờ người xử lý.",
  severity: "BLOCKING",
  primaryRoute: "/ho-so/case-block/quy-trinh",
  createdAt: "2026-07-18T08:00:00Z",
};

const attentionItem = {
  caseId: "case-gap",
  caseVersion: 1,
  kind: "GAP_BATCH_PENDING",
  titleVi: "Duyệt yêu cầu bổ sung",
  reasonVi: "Có khoảng trống bằng chứng đang mở.",
  severity: "ATTENTION",
  primaryRoute: "/ho-so/case-gap/khoang-trong",
  createdAt: "2026-07-18T09:00:00Z",
};

// Both the kind and the severity are outside the known vocabulary.
const unknownItem = {
  caseId: "case-x",
  caseVersion: 5,
  kind: "SOME_FUTURE_KIND",
  titleVi: "Việc kiểu mới",
  reasonVi: "Máy chủ trả về loại chưa được hỗ trợ.",
  severity: "CRITICAL",
  primaryRoute: "/ho-so/case-x/quy-trinh",
  createdAt: "2026-07-18T10:00:00Z",
};

describe("WorkQueue", () => {
  it("shows a loading state while the queue is fetching", () => {
    const api = { listWorkItems: vi.fn().mockReturnValue(new Promise<never>(() => {})) };

    render(<WorkQueue api={api} />);

    expect(screen.getByLabelText("Đang tải hàng việc")).toBeVisible();
  });

  it("renders the scoped empty state when there are no items", async () => {
    const api = { listWorkItems: vi.fn().mockResolvedValue(list([])) };

    render(<WorkQueue api={api} />);

    expect(
      await screen.findByText("Không có việc cần xử lý trong phạm vi phân công."),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Làm mới" })).not.toBeInTheDocument();
  });

  it("surfaces a Vietnamese error and retries on demand", async () => {
    const api = {
      listWorkItems: vi
        .fn()
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValueOnce(list([attentionItem])),
    };

    render(<WorkQueue api={api} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Không thể hoàn tất yêu cầu. Vui lòng thử lại.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));

    expect(await screen.findByText("Duyệt yêu cầu bổ sung")).toBeVisible();
    expect(api.listWorkItems).toHaveBeenCalledTimes(2);
  });

  it("groups blocking items ahead of the rest with severity chips", async () => {
    // Server order deliberately puts the attention item first; grouping must
    // still surface the blocking item at the top of the list.
    const api = {
      listWorkItems: vi.fn().mockResolvedValue(list([attentionItem, blockingItem])),
    };

    render(<WorkQueue api={api} />);
    await screen.findByText("Cần rà soát thủ công");

    expect(screen.getByRole("heading", { name: "Cần xử lý trước" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Các việc khác" })).toBeVisible();

    const titles = screen
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(titles).toEqual(["Cần rà soát thủ công", "Duyệt yêu cầu bổ sung"]);

    expect(screen.getByText("Chặn xử lý")).toBeVisible();
    expect(screen.getByText("Cần xử lý")).toBeVisible();
  });

  it("keeps an unknown-enum item but fails closed to the unsupported label", async () => {
    const api = { listWorkItems: vi.fn().mockResolvedValue(list([unknownItem])) };

    render(<WorkQueue api={api} />);

    // The item is kept — its server-supplied text still renders.
    expect(await screen.findByText("Việc kiểu mới")).toBeVisible();
    // But the enum-derived chip never guesses a severity.
    expect(screen.getByText("Trạng thái chưa được hỗ trợ")).toBeVisible();
    expect(screen.queryByText("Chặn xử lý")).not.toBeInTheDocument();
    // And an item we do not understand is never elevated into the blocking group.
    expect(
      screen.queryByRole("heading", { name: "Cần xử lý trước" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Các việc khác" })).toBeVisible();
  });

  it("links each item to its primaryRoute verbatim", async () => {
    const api = {
      listWorkItems: vi
        .fn()
        .mockResolvedValue(list([blockingItem, attentionItem, unknownItem])),
    };

    render(<WorkQueue api={api} />);

    expect(
      await screen.findByRole("link", { name: "Mở việc — Cần rà soát thủ công" }),
    ).toHaveAttribute("href", "/ho-so/case-block/quy-trinh");
    expect(
      screen.getByRole("link", { name: "Mở việc — Duyệt yêu cầu bổ sung" }),
    ).toHaveAttribute("href", "/ho-so/case-gap/khoang-trong");
    // Unsupported items still expose their exact server route, never a guess.
    expect(
      screen.getByRole("link", { name: "Mở việc — Việc kiểu mới" }),
    ).toHaveAttribute("href", "/ho-so/case-x/quy-trinh");
  });

  it("refetches when the manual refresh button is used", async () => {
    const api = {
      listWorkItems: vi
        .fn()
        .mockResolvedValueOnce(list([attentionItem]))
        .mockResolvedValueOnce(list([blockingItem])),
    };

    render(<WorkQueue api={api} />);
    expect(await screen.findByText("Duyệt yêu cầu bổ sung")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Làm mới" }));

    expect(await screen.findByText("Cần rà soát thủ công")).toBeVisible();
    expect(api.listWorkItems).toHaveBeenCalledTimes(2);
  });
});
