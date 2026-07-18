// -----------------------------------------------------------------------------
// Synthetic scenario fixture layer — shared types.
//
// This module powers the Scenario Switcher (components/scenario). When a
// scenario is active, a client-side fetch interceptor answers /api/creditops/*
// requests from an in-memory synthetic store INSTEAD of the live upstream, so a
// tester can drive the real 14-stage UI offline. Every response mirrors the
// exact DTO / error shape the upstream would return, so the real API-client
// stack (schema parsing, the 401/403/409/429/503 taxonomy, CTA gating) is
// exercised — not bypassed.
//
// NON-NEGOTIABLE: this is a SYNTHETIC TEST FIXTURE. It never masquerades as a
// live backend — the switcher labels it as such, and it is off by default.
// -----------------------------------------------------------------------------

export type ScenarioId =
  | "clean-complete"
  | "missing-documents"
  | "conflicting-facts"
  | "document-quality"
  | "risk-challenge"
  | "downstream-stale"
  | "policy-unavailable"
  | "unauthorized-access"
  | "pending-condition"
  | "execution-unknown"
  | "repayment-anomaly"
  | "settlement-recovery";

// The workflow stage a scenario spotlights, used to deep-link the tester to the
// workspace where the agent-under-test does its work. Values match route
// segments under /ho-so/[caseId]/.
export type FocusSection =
  | "tiep-nhan"
  | "tai-lieu"
  | "doi-chieu"
  | "khoang-trong"
  | "tham-dinh"
  | "phap-che"
  | "rui-ro"
  | "tong-hop"
  | "thong-bao"
  | "hop-dong"
  | "bao-dam"
  | "dieu-kien-giai-ngan"
  | "giai-ngan"
  | "giam-sat"
  | "thu-no"
  | "tat-toan-xu-ly-no"
  | "ban-giao"
  | "nhat-ky";

export type AssertionStatus = "pending" | "pass" | "fail";

export interface AssertionResult {
  status: AssertionStatus;
  // Human-readable Vietnamese description of the CURRENT (actual) state, shown
  // beside the expected result so a tester sees the delta.
  actual: string;
}

// A deterministic mock transition the tester can trigger from the switcher.
// It is NOT live agent inference — it drives a fixed, synthetic state change so
// each function can be exercised without the upstream backend.
export interface TestControl {
  id: string;
  label: string;
  // Short Vietnamese note describing the deterministic effect.
  effect: string;
}

export interface ScenarioDefinition {
  id: ScenarioId;
  // Ordinal shown in the switcher (1..12), matching the brief's scenario list.
  ordinal: number;
  title: string;
  // One-line initial state (trạng thái ban đầu).
  initialState: string;
  // Which agent role is under test (agent nào đang được test).
  agentUnderTest: string;
  // The human gate that must be exercised (human gate cần thực hiện).
  humanGate: string;
  // What a passing run should produce (expected result).
  expectedResult: string;
  // Evidence references the scenario surfaces (evidence refs).
  evidenceRefs: readonly string[];
  // The audit event a successful run should record (audit event).
  auditEvent: string;
  // Where to send the tester to exercise the scenario.
  focusSection: FocusSection;
  // When focusSection is "tai-lieu", the document to open.
  focusDocumentId?: string;
  // Deterministic controls the switcher offers for this scenario.
  testControls: readonly TestControl[];
}

// -----------------------------------------------------------------------------
// Fixture routing
// -----------------------------------------------------------------------------

export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export interface FixtureRequest {
  method: HttpMethod;
  // Path AFTER the /api/creditops prefix, e.g. "/api/v1/cases/{id}/evidence".
  path: string;
  // Path split on "/" with empty segments removed.
  segments: string[];
  // Parsed query string.
  query: URLSearchParams;
  // Parsed JSON body for mutations, or null.
  body: unknown;
  // Request headers (Idempotency-Key, etc.).
  headers: Headers;
}

export interface FixtureResponse {
  status: number;
  // JSON-serialisable body, or null for an empty body.
  body: unknown;
  // Extra response headers (e.g. Retry-After for 429).
  headers?: Record<string, string>;
}

// A handler owns a family of routes for one domain. It receives the parsed
// request and the mutable scenario store, and returns a response, or null to
// signal "not my route" so the router can try the next handler.
export type FixtureHandler = (
  request: FixtureRequest,
  store: import("./store").FixtureStore,
) => FixtureResponse | null;

export function apiError(
  status: number,
  code: string,
  messageVi: string,
  extra: { retryable?: boolean; details?: Record<string, unknown> } = {},
): FixtureResponse {
  return {
    status,
    body: {
      code,
      messageVi,
      correlationId: null,
      retryable: extra.retryable ?? status >= 500,
      ...(extra.details ? { details: extra.details } : {}),
    },
  };
}
