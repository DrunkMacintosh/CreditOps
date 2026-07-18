import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { MonitoringWorkspace } from "../../components/monitoring/monitoring-workspace";
import { ApiClientError } from "../../lib/api/client";
import type {
  Alert,
  AlertList,
  Covenant,
  CovenantTest,
  MonitoringApiClient,
  Obligation,
  Observation,
} from "../../lib/api/monitoring";

type FakeApi = Pick<
  MonitoringApiClient,
  | "listObligations"
  | "createObligations"
  | "listObservations"
  | "recordObservation"
  | "listCovenants"
  | "createCovenant"
  | "runCovenantTest"
  | "listCovenantTests"
  | "listAlerts"
  | "disposeAlert"
>;

function buildAlert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: "alert-1",
    caseId: "case-1",
    caseVersion: 5,
    rule: "COVENANT_BREACH",
    status: "OPEN",
    detail: "Hệ số thanh toán hiện hành dưới ngưỡng cam kết.",
    sourceCovenantTestId: "test-1",
    sourceObligationId: null,
    sourceObservationId: null,
    createdAt: "2026-07-18T08:00:00Z",
    ...overrides,
  };
}

function buildObligation(overrides: Partial<Obligation> = {}): Obligation {
  return {
    id: "obl-1",
    caseId: "case-1",
    caseVersion: 5,
    sequence: 1,
    frequency: "MONTHLY",
    dueDate: "2026-08-18",
    requirementText: "Nộp báo cáo tài chính hằng tháng",
    createdAt: "2026-07-18T08:00:00Z",
    ...overrides,
  };
}

function buildObservation(overrides: Partial<Observation> = {}): Observation {
  return {
    id: "obs-1",
    caseId: "case-1",
    caseVersion: 5,
    obligationId: null,
    observationType: "Kiểm tra thực địa",
    body: "Doanh nghiệp vận hành bình thường.",
    effectiveAt: "2026-07-01T00:00:00Z",
    observedAt: "2026-07-10T00:00:00Z",
    recordedAt: "2026-07-18T08:00:00Z",
    evidenceRefs: [],
    ...overrides,
  };
}

function buildCovenant(overrides: Partial<Covenant> = {}): Covenant {
  return {
    id: "cov-1",
    caseId: "case-1",
    caseVersion: 5,
    name: "Hệ số thanh toán hiện hành",
    metricKey: "current_ratio",
    operator: "GTE",
    thresholdValue: "1.2",
    thresholdVersion: 1,
    createdAt: "2026-07-18T08:00:00Z",
    ...overrides,
  };
}

function buildCovenantTest(overrides: Partial<CovenantTest> = {}): CovenantTest {
  return {
    id: "test-1",
    covenantId: "cov-1",
    caseId: "case-1",
    caseVersion: 5,
    metricKey: "current_ratio",
    operator: "GTE",
    numerator: "1.5",
    denominator: "1.0",
    thresholdValue: "1.2",
    thresholdVersion: 1,
    comparisonLhs: "1.5",
    comparisonRhs: "1.2",
    passed: true,
    recordedAt: "2026-07-18T08:00:00Z",
    ...overrides,
  };
}

function fakeApi(
  data: {
    obligations?: Obligation[];
    observations?: Observation[];
    covenants?: Covenant[];
    covenantTests?: CovenantTest[];
    alerts?: Alert[];
  } = {},
  overrides: Partial<FakeApi> = {},
): FakeApi {
  return {
    listObligations: vi.fn(async () => ({
      obligations: data.obligations ?? [],
      caseVersion: 5,
    })),
    createObligations: vi.fn(async () => ({ obligations: [], caseVersion: 5 })),
    listObservations: vi.fn(async () => ({
      observations: data.observations ?? [],
      caseVersion: 5,
    })),
    recordObservation: vi.fn(async () => ({ observation: buildObservation(), alert: null })),
    listCovenants: vi.fn(async () => ({ covenants: data.covenants ?? [], caseVersion: 5 })),
    createCovenant: vi.fn(async () => buildCovenant()),
    runCovenantTest: vi.fn(async () => ({ test: buildCovenantTest(), alert: null })),
    listCovenantTests: vi.fn(async () => ({
      tests: data.covenantTests ?? [],
      caseVersion: 5,
    })),
    listAlerts: vi.fn(async () => ({ alerts: data.alerts ?? [], caseVersion: 5 })),
    disposeAlert: vi.fn(async () => buildAlert({ status: "ACKNOWLEDGED" })),
    ...overrides,
  };
}

describe("MonitoringWorkspace — states", () => {
  it("shows the loading skeleton before the requests resolve", () => {
    const api = fakeApi();
    api.listAlerts = vi.fn(() => new Promise<AlertList>(() => {}));
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    expect(screen.getByLabelText("Đang tải dữ liệu giám sát")).toBeVisible();
  });

  it("shows an error and a retry when a load fails", async () => {
    const api = fakeApi();
    api.listAlerts = vi.fn().mockRejectedValue(new Error("network"));
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Thử tải lại" }));
    await waitFor(() => expect(api.listAlerts).toHaveBeenCalledTimes(2));
  });

  it("renders NO controls on a 403 load", async () => {
    const api = fakeApi();
    api.listObligations = vi
      .fn()
      .mockRejectedValue(new ApiClientError(403, "INSUFFICIENT_ROLE", "", false));
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    expect(
      await screen.findByText("Bạn không có vai trò tham gia hồ sơ để xem dữ liệu giám sát."),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Khai báo cam kết" })).not.toBeInTheDocument();
  });

  it("renders an obligation with its due date and Vietnamese frequency", async () => {
    const api = fakeApi({ obligations: [buildObligation()] });
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/Nộp báo cáo tài chính hằng tháng/)).toBeVisible();
    // "Hằng tháng" also appears as a <select> option; assert the chip label exists.
    expect(screen.getAllByText("Hằng tháng").length).toBeGreaterThan(0);
  });

  it("renders an observation with THREE distinct timestamps", async () => {
    const api = fakeApi({ observations: [buildObservation()] });
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/^Hiệu lực:/)).toBeVisible();
    expect(screen.getByText(/^Quan sát:/)).toBeVisible();
    expect(screen.getByText(/^Ghi nhận:/)).toBeVisible();
  });

  it("renders a covenant test with the echoed arithmetic and pass verdict", async () => {
    const api = fakeApi({
      covenants: [buildCovenant()],
      covenantTests: [buildCovenantTest()],
    });
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    expect(await screen.findByText(/so sánh 1\.5 ≥ 1\.2/)).toBeVisible();
    expect(screen.getByText("Đạt")).toBeVisible();
  });

  it("fails closed on an unknown alert status (unsupported label, no disposition)", async () => {
    const api = fakeApi({ alerts: [buildAlert({ status: "WEIRD_STATE" })] });
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    expect(await screen.findAllByText(/Trạng thái chưa được hỗ trợ/)).not.toHaveLength(0);
    expect(
      screen.getByText("Trạng thái kết thúc: không còn bước xử lý."),
    ).toBeVisible();
  });
});

describe("MonitoringWorkspace — alert disposition", () => {
  it("does not preselect a disposition target and requires one", async () => {
    const api = fakeApi({ alerts: [buildAlert()] });
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Hệ số thanh toán hiện hành dưới ngưỡng cam kết.");
    expect(screen.getByRole("radio", { name: "Tiếp nhận cảnh báo" })).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Ghi nhận xử lý cảnh báo" }));
    expect(screen.getByText("Chọn hướng xử lý cảnh báo.")).toBeVisible();
    expect(api.disposeAlert).not.toHaveBeenCalled();
  });

  it("requires a rationale after a target is chosen", async () => {
    const api = fakeApi({ alerts: [buildAlert()] });
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Hệ số thanh toán hiện hành dưới ngưỡng cam kết.");
    await userEvent.click(screen.getByRole("radio", { name: "Chuyển cấp cảnh báo" }));
    await userEvent.click(screen.getByRole("button", { name: "Ghi nhận xử lý cảnh báo" }));
    expect(
      screen.getByText("Xử lý cảnh báo là quyết định có thẩm quyền: bắt buộc nhập lý do."),
    ).toBeVisible();
    expect(api.disposeAlert).not.toHaveBeenCalled();
  });

  it("only offers disposition edges allowed from the current status", async () => {
    const api = fakeApi({ alerts: [buildAlert({ status: "ESCALATED" })] });
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Hệ số thanh toán hiện hành dưới ngưỡng cam kết.");
    // From ESCALATED the only allowed edge is DISMISSED_BY_HUMAN.
    expect(screen.getByRole("radio", { name: "Đóng cảnh báo (ghi thẩm quyền)" })).toBeVisible();
    expect(screen.queryByRole("radio", { name: "Tiếp nhận cảnh báo" })).not.toBeInTheDocument();
  });

  it("records a disposition with the chosen target + rationale and refetches", async () => {
    const api = fakeApi({ alerts: [buildAlert()] });
    render(<MonitoringWorkspace api={api} caseId="case-1" />);
    await screen.findByText("Hệ số thanh toán hiện hành dưới ngưỡng cam kết.");
    await userEvent.click(screen.getByRole("radio", { name: "Tiếp nhận cảnh báo" }));
    await userEvent.type(screen.getByLabelText(/Lý do/), "Đã phân công cán bộ theo dõi.");
    await userEvent.click(screen.getByRole("button", { name: "Ghi nhận xử lý cảnh báo" }));
    await waitFor(() =>
      expect(api.disposeAlert).toHaveBeenCalledWith("case-1", "alert-1", {
        toStatus: "ACKNOWLEDGED",
        rationale: "Đã phân công cán bộ theo dõi.",
      }),
    );
    await waitFor(() => expect(api.listAlerts).toHaveBeenCalledTimes(2));
  });
});
