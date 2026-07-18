import { render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { RiskReviewDesk } from "../../components/risk/risk-review-desk";
import { ApiClientError } from "../../lib/api/client";
import type {
  Challenge,
  RiskReviewApiClient,
  RiskReviewStatus,
} from "../../lib/api/risk-review";

// Characterization tests for the independent risk review desk ("Bàn rà soát
// thách thức"). The component takes its api as a Pick<RiskReviewApiClient, ...>
// prop with a default of the real client, so — exactly like the
// orchestration-console and legal-assessment suites — we inject a fake api
// object directly rather than vi.mock the module boundary.

type FakeApi = Pick<
  RiskReviewApiClient,
  "getRiskReview" | "recordChallengeDisposition" | "recordAssessmentDisposition"
>;

function buildChallenge(overrides: Partial<Challenge> = {}): Challenge {
  return {
    id: "chal-1",
    target: {
      makerSource: "CREDIT_UNDERWRITING",
      makerAssessmentId: "uw-1",
      sectionPath: "repayment_source",
    },
    challengeType: "OMITTED_RISK",
    statement: "Chưa đề cập rủi ro tập trung khách hàng.",
    citations: [{ kind: "CONFIRMED_FACT", confirmedFactId: "fact-1" }],
    severity: "HIGH",
    confidence: "MEDIUM",
    raisedBy: "LLM",
    dispositions: [],
    ...overrides,
  };
}

function buildStatus(overrides: Partial<RiskReviewStatus> = {}): RiskReviewStatus {
  return {
    assessmentId: "assess-1",
    caseId: "case-1",
    caseVersion: 2,
    agentRole: "INDEPENDENT_RISK_REVIEW",
    executionId: "exec-1",
    promptVersion: "risk-review-prompt-v1",
    createdAt: "2026-07-18T08:00:00Z",
    handoff: null,
    challenges: [buildChallenge()],
    assessmentLevelDispositions: [],
    unresolvedChallengeCount: 1,
    gateStatus: "OPEN",
    ...overrides,
  };
}

function fakeApi(overrides: Partial<FakeApi> = {}): FakeApi {
  return {
    getRiskReview: vi.fn(async () => buildStatus()),
    recordChallengeDisposition: vi.fn(async () => ({
      id: "disp-1",
      dispositionType: "ACCEPTED_RISK",
      rationale: "ok",
      actorId: "reviewer-1",
      actorRole: "INDEPENDENT_RISK_REVIEWER",
      createdAt: "2026-07-18T09:00:00Z",
    })),
    recordAssessmentDisposition: vi.fn(async () => ({
      id: "disp-2",
      dispositionType: "NOTED",
      rationale: "ok",
      actorId: "reviewer-1",
      actorRole: "INDEPENDENT_RISK_REVIEWER",
      createdAt: "2026-07-18T09:00:00Z",
    })),
    ...overrides,
  };
}

describe("RiskReviewDesk — loading, error, 404, and 403 states", () => {
  it("shows the loading skeleton before the request resolves", () => {
    const api = fakeApi({
      getRiskReview: vi.fn(() => new Promise<RiskReviewStatus>(() => {})),
    });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    expect(screen.getByLabelText("Đang tải bản rà soát rủi ro")).toBeVisible();
  });

  it("shows a Vietnamese error and no data on API failure — no silent mock fallback", async () => {
    const api = fakeApi({
      getRiskReview: vi.fn().mockRejectedValue(new Error("network down")),
    });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Không thể hoàn tất yêu cầu. Vui lòng thử lại.");
    expect(screen.queryByText("Cổng rà soát rủi ro (G3)")).not.toBeInTheDocument();
    expect(screen.queryByText("Chưa đề cập rủi ro tập trung khách hàng.")).not.toBeInTheDocument();
  });

  it("retries the load when 'Thử tải lại' is clicked after an error", async () => {
    const api = fakeApi({
      getRiskReview: vi
        .fn()
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValueOnce(buildStatus()),
    });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));

    await waitFor(() => expect(api.getRiskReview).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Cổng rà soát rủi ro (G3)")).toBeVisible();
  });

  it("shows the not-available empty state (404) when no assessment exists yet", async () => {
    const api = fakeApi({
      getRiskReview: vi
        .fn()
        .mockRejectedValue(new ApiClientError(404, "RISK_REVIEW_NOT_AVAILABLE", "", false)),
    });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    expect(
      await screen.findByText("Chưa có bản rà soát rủi ro độc lập"),
    ).toBeVisible();
    expect(screen.queryByText("Cổng rà soát rủi ro (G3)")).not.toBeInTheDocument();
  });

  it("shows the role-specific Vietnamese message on a 403 (no distinct capability model beyond the generic error branch)", async () => {
    const api = fakeApi({
      getRiskReview: vi
        .fn()
        .mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false)),
    });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    expect(
      await screen.findByText(
        "Bạn không có vai trò rà soát rủi ro độc lập để ghi quyết định.",
      ),
    ).toBeVisible();
    // No mutation surfaces render at all — the assessment itself never loaded.
    expect(screen.queryByRole("button", { name: /Ghi quyết định/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ghi nhận/ })).not.toBeInTheDocument();
  });
});

describe("RiskReviewDesk — challenges render with severity, most severe first", () => {
  it("renders each challenge's severity chip and orders CRITICAL before LOW", async () => {
    const status = buildStatus({
      challenges: [
        buildChallenge({
          id: "chal-low",
          severity: "LOW",
          statement: "Thiếu chi tiết nhỏ về lịch sử thanh toán.",
        }),
        buildChallenge({
          id: "chal-crit",
          severity: "CRITICAL",
          statement: "Bỏ sót rủi ro tập trung nghiêm trọng vào một khách hàng.",
        }),
      ],
      unresolvedChallengeCount: 2,
    });
    const api = fakeApi({ getRiskReview: vi.fn(async () => status) });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    await screen.findByText("Thiếu chi tiết nhỏ về lịch sử thanh toán.");
    expect(screen.getByText("Mức Thấp")).toBeVisible();
    expect(screen.getByText("Mức Nghiêm trọng")).toBeVisible();

    const articles = screen.getAllByRole("article");
    expect(articles).toHaveLength(2);
    // CRITICAL outranks LOW, so it must render first regardless of input order.
    expect(articles[0]).toHaveTextContent("Bỏ sót rủi ro tập trung nghiêm trọng");
    expect(articles[1]).toHaveTextContent("Thiếu chi tiết nhỏ về lịch sử thanh toán");
  });

  it("shows the gate metrics counting severe (HIGH/CRITICAL) challenges only", async () => {
    const status = buildStatus({
      challenges: [
        buildChallenge({ id: "chal-low", severity: "LOW" }),
        buildChallenge({ id: "chal-high", severity: "HIGH" }),
      ],
      unresolvedChallengeCount: 2,
    });
    const api = fakeApi({ getRiskReview: vi.fn(async () => status) });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    await screen.findByText("Cổng rà soát rủi ro (G3)");
    expect(screen.getByText("0/1")).toBeVisible(); // severeDisposed/severeTotal
    expect(screen.getByText("Đang chờ")).toBeVisible(); // gate not satisfied
  });
});

describe("RiskReviewDesk — per-challenge disposition form", () => {
  it("has no disposition type preselected: all radios start unchecked, and submitting without one does not call the API", async () => {
    const api = fakeApi();
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    await screen.findByText("Chưa đề cập rủi ro tập trung khách hàng.");

    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios.length).toBeGreaterThan(0);
    for (const radio of radios) {
      expect(radio.checked).toBe(false);
    }

    await userEvent.type(
      screen.getByLabelText(/Lý do quyết định/),
      "Đã xem xét thách thức này.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Ghi quyết định" }));

    expect(screen.getByText("Chọn một loại quyết định trước khi ghi.")).toBeVisible();
    expect(api.recordChallengeDisposition).not.toHaveBeenCalled();
  });

  it("requires a rationale even once a disposition type is selected", async () => {
    const api = fakeApi();
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    await screen.findByText("Chưa đề cập rủi ro tập trung khách hàng.");
    await userEvent.click(screen.getByLabelText("Chấp nhận rủi ro"));
    await userEvent.click(screen.getByRole("button", { name: "Ghi quyết định" }));

    expect(
      screen.getByText("Nhập lý do cho quyết định; đây là trường bắt buộc."),
    ).toBeVisible();
    expect(api.recordChallengeDisposition).not.toHaveBeenCalled();
  });

  it("calls the API with the selected type and rationale, then re-fetches", async () => {
    const api = fakeApi();
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    await screen.findByText("Chưa đề cập rủi ro tập trung khách hàng.");
    await userEvent.click(screen.getByLabelText("Yêu cầu bên lập chỉnh sửa"));
    await userEvent.type(
      screen.getByLabelText(/Lý do quyết định/),
      "Bên lập cần bổ sung phân tích rủi ro tập trung.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Ghi quyết định" }));

    await waitFor(() =>
      expect(api.recordChallengeDisposition).toHaveBeenCalledWith("case-1", "chal-1", {
        dispositionType: "MAKER_MUST_REVISE",
        rationale: "Bên lập cần bổ sung phân tích rủi ro tập trung.",
      }),
    );
    await waitFor(() => expect(api.getRiskReview).toHaveBeenCalledTimes(2));
  });

  it("shows the distinct 'recorded but could not reload' message when the write succeeds but the refresh fails", async () => {
    const api = fakeApi({
      getRiskReview: vi
        .fn()
        .mockResolvedValueOnce(buildStatus())
        .mockRejectedValueOnce(new Error("read hiccup")),
    });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    await screen.findByText("Chưa đề cập rủi ro tập trung khách hàng.");
    await userEvent.click(screen.getByLabelText("Chấp nhận rủi ro"));
    await userEvent.type(
      screen.getByLabelText(/Lý do quyết định/),
      "Rủi ro đã được xử lý bằng biện pháp khác.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Ghi quyết định" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Quyết định đã được ghi, nhưng không tải lại được bản mới nhất: Không thể hoàn tất yêu cầu. Vui lòng thử lại.",
    );
    expect(api.recordChallengeDisposition).toHaveBeenCalledTimes(1);
  });
});

describe("RiskReviewDesk — assessment-level disposition (NOTED-only)", () => {
  it("shows the fixed-type NOTED form only when there are no severe challenges and nothing recorded yet", async () => {
    const status = buildStatus({
      challenges: [buildChallenge({ severity: "LOW" })],
      unresolvedChallengeCount: 0,
      assessmentLevelDispositions: [],
    });
    const api = fakeApi({ getRiskReview: vi.fn(async () => status) });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    expect(
      await screen.findByText("Ghi nhận khi không có thách thức nghiêm trọng"),
    ).toBeVisible();

    const submitButton = screen.getByRole("button", { name: "Ghi nhận kết quả rà soát" });
    const assessmentForm = submitButton.closest("form");
    expect(assessmentForm).not.toBeNull();
    const scoped = within(assessmentForm as HTMLElement);
    // Fixed type: shows "Loại quyết định: Ghi nhận" as plain text, not a
    // selectable radio group (the still-present per-challenge form for the
    // LOW-severity challenge does have its own radio group elsewhere on the
    // page — including its own "Ghi nhận" option label — so every assertion
    // here must stay scoped to this specific form).
    expect(scoped.getByText(/Loại quyết định:/)).toBeVisible();
    expect(scoped.getByText("Ghi nhận")).toBeVisible();
    expect(scoped.queryAllByRole("radio")).toHaveLength(0);

    await userEvent.type(
      screen.getByLabelText(/Nội dung ghi nhận/),
      "Không có thách thức mức cao; rà soát hoàn tất.",
    );
    await userEvent.click(submitButton);

    await waitFor(() =>
      expect(api.recordAssessmentDisposition).toHaveBeenCalledWith("case-1", {
        dispositionType: "NOTED",
        rationale: "Không có thách thức mức cao; rà soát hoàn tất.",
      }),
    );
    await waitFor(() => expect(api.getRiskReview).toHaveBeenCalledTimes(2));
  });

  it("does not show the assessment-level form once a disposition is already recorded", async () => {
    const status = buildStatus({
      challenges: [buildChallenge({ severity: "LOW" })],
      unresolvedChallengeCount: 0,
      assessmentLevelDispositions: [
        {
          id: "disp-recorded",
          dispositionType: "NOTED",
          rationale: "Đã rà soát, không có vấn đề nghiêm trọng.",
          actorId: "reviewer-1",
          actorRole: "INDEPENDENT_RISK_REVIEWER",
          createdAt: "2026-07-18T09:00:00Z",
        },
      ],
    });
    const api = fakeApi({ getRiskReview: vi.fn(async () => status) });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    expect(
      await screen.findByText("Đã rà soát, không có vấn đề nghiêm trọng."),
    ).toBeVisible();
    expect(
      screen.queryByText("Ghi nhận khi không có thách thức nghiêm trọng"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Ghi nhận kết quả rà soát" }),
    ).not.toBeInTheDocument();
  });

  it("does not show the assessment-level form while a severe challenge is still undisposed", async () => {
    const status = buildStatus({
      challenges: [buildChallenge({ severity: "HIGH" })],
      unresolvedChallengeCount: 1,
      assessmentLevelDispositions: [],
    });
    const api = fakeApi({ getRiskReview: vi.fn(async () => status) });
    render(<RiskReviewDesk api={api} caseId="case-1" />);

    await screen.findByText("Chưa đề cập rủi ro tập trung khách hàng.");
    expect(
      screen.queryByText("Ghi nhận khi không có thách thức nghiêm trọng"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Có thách thức mức Cao/Nghiêm trọng cần quyết định riêng ở trên; chưa cần ghi nhận ở cấp bản đánh giá.",
      ),
    ).toBeVisible();
  });
});
