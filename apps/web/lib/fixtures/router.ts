// -----------------------------------------------------------------------------
// Fixture router — turns a parsed request into a response using the registered
// domain handlers, then an honest "not available / empty state" fallback.
//
// dispatch() is pure and independently testable: tests call it directly with a
// FixtureStore, no fetch patching required.
// -----------------------------------------------------------------------------

import { coreHandlers } from "./handlers/core";
import { creditOpsHandlers } from "./handlers/credit-ops";
import { stageHandlers } from "./handlers/stages";
import { underwritingHandlers } from "./handlers/underwriting";
import type { FixtureStore } from "./store";
import { apiError, type FixtureRequest, type FixtureResponse } from "./types";

const HANDLERS = [
  ...coreHandlers,
  ...stageHandlers,
  ...underwritingHandlers,
  ...creditOpsHandlers,
];

// GET stage reads whose "not available" 404 the client treats as a clean empty
// state (never a hard error). When a scenario does not populate a stage, we
// answer with its canonical not-available code so the workspace shows its empty
// state honestly rather than fabricating data.
const NOT_AVAILABLE: { test: (r: FixtureRequest) => boolean; code: string }[] = [
  { test: (r) => tail(r) === "underwriting", code: "UNDERWRITING_NOT_AVAILABLE" },
  { test: (r) => tail(r) === "legal", code: "LEGAL_ASSESSMENT_NOT_AVAILABLE" },
  { test: (r) => tail(r) === "risk-review", code: "RISK_REVIEW_NOT_AVAILABLE" },
  { test: (r) => tail(r) === "credit-ops", code: "CREDIT_OPS_NOT_AVAILABLE" },
  { test: (r) => tail(r) === "gap-request-batches", code: "GAP_REQUEST_BATCH_NOT_AVAILABLE" },
  { test: (r) => tail(r) === "contract-packages", code: "NO_CONTRACT_PACKAGE" },
];

function tail(r: FixtureRequest): string {
  return r.segments[r.segments.length - 1] ?? "";
}

export function dispatch(request: FixtureRequest, store: FixtureStore): FixtureResponse {
  // Global authorisation gates that apply before any handler.
  if (store.flags.crossCaseHidden && isCaseScoped(request)) {
    // Never reveal whether the case exists.
    return apiError(404, "CASE_NOT_FOUND", "Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.");
  }

  for (const handler of HANDLERS) {
    const response = handler(request, store);
    if (response) return response;
  }

  if (request.method === "GET") {
    for (const rule of NOT_AVAILABLE) {
      if (rule.test(request)) {
        return apiError(404, rule.code, "Chưa có dữ liệu cho bước này.");
      }
    }
    // Empty list-shaped reads default to a benign empty body so unfocused
    // stages render their empty state instead of erroring.
    return { status: 200, body: emptyBodyFor(request) };
  }

  return apiError(404, "FIXTURE_ROUTE_NOT_FOUND", "Chức năng này chưa được mô phỏng trong kịch bản.");
}

function isCaseScoped(request: FixtureRequest): boolean {
  return request.segments[2] === "cases" && typeof request.segments[3] === "string";
}

// Best-effort empty shapes for list reads not owned by a handler, so unfocused
// workspaces (monitoring, settlement, security, conditions…) render cleanly.
function emptyBodyFor(request: FixtureRequest): unknown {
  const last = tail(request);
  switch (last) {
    case "obligations":
      return { obligations: [], caseVersion: 1 };
    case "observations":
      return { observations: [], caseVersion: 1 };
    case "covenants":
      return { covenants: [], caseVersion: 1 };
    case "covenant-tests":
      return { tests: [], caseVersion: 1 };
    case "alerts":
      return { alerts: [], caseVersion: 1 };
    case "security-interests":
      return { interests: [] };
    case "conditions":
      return { conditions: [], caseVersion: 1, confirmable: false };
    case "proposed-disbursements":
      return { actions: [], caseVersion: 1 };
    case "settlement":
      return { checks: [], receipts: [], caseVersion: 1, confirmable: false };
    case "recovery":
      return { recoveryCases: [], caseVersion: 1 };
    case "notifications":
      return { draft: null, receipt: null, approvalGateStatus: "OPEN" };
    default:
      return null;
  }
}
