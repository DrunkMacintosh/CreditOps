// Self-contained API bindings for the stage-12 post-credit monitoring workspace
// ("Giám sát sau cấp tín dụng"). Mirrors lib/api/client.ts conventions but shares
// no mutable state with it — the gap-requests.ts self-contained pattern.
//
// Backend truth mirrored here: services/api/src/creditops/api/monitoring.py and
// services/.../domain/monitoring.py.
//   POST/GET /api/v1/cases/{caseId}/monitoring/obligations      -> obligations (deterministic schedule)
//   POST/GET /api/v1/cases/{caseId}/monitoring/observations     -> append-only longitudinal observation (+ OVERDUE alert)
//   POST/GET /api/v1/cases/{caseId}/monitoring/covenants        -> covenant carrying its own versioned threshold
//   POST     /api/v1/cases/{caseId}/monitoring/covenants/{id}/test -> covenant test with echoed arithmetic (+ BREACH alert)
//   GET      /api/v1/cases/{caseId}/monitoring/covenant-tests   -> recorded tests
//   GET      /api/v1/cases/{caseId}/monitoring/alerts           -> early-warning alerts
//   POST     /api/v1/cases/{caseId}/monitoring/alerts/{id}/disposition -> human disposition (validated edge, rationale required)
//
// Every early-warning alert is raised by a DETERMINISTIC rule, never a model. NO
// debt classification anywhere. Alert dispositions move along a closed lifecycle;
// an unknown enum fails closed.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/monitoring.py) ---

export type ObligationFrequency = "MONTHLY" | "QUARTERLY";

export const OBLIGATION_FREQUENCIES: readonly ObligationFrequency[] = [
  "MONTHLY",
  "QUARTERLY",
];

export type ComparisonOperator = "GTE" | "GT" | "LTE" | "LT" | "EQ";

export const COMPARISON_OPERATORS: readonly ComparisonOperator[] = [
  "GTE",
  "GT",
  "LTE",
  "LT",
  "EQ",
];

export type AlertRule = "COVENANT_BREACH" | "OVERDUE_OBLIGATION";

export type AlertStatus =
  | "OPEN"
  | "ACKNOWLEDGED"
  | "ESCALATED"
  | "DISMISSED_BY_HUMAN";

// The order alerts are grouped in for display.
export const ALERT_STATUSES: readonly AlertStatus[] = [
  "OPEN",
  "ACKNOWLEDGED",
  "ESCALATED",
  "DISMISSED_BY_HUMAN",
];

// The deterministic disposition map (mirrors ALLOWED_ALERT_TRANSITIONS). The UI
// derives each alert's target choices from its current status, so it never offers
// a forbidden edge; the server re-validates regardless. OPEN is never a target.
export const ALLOWED_ALERT_TRANSITIONS: Record<AlertStatus, readonly AlertStatus[]> = {
  OPEN: ["ACKNOWLEDGED", "ESCALATED", "DISMISSED_BY_HUMAN"],
  ACKNOWLEDGED: ["ESCALATED", "DISMISSED_BY_HUMAN"],
  ESCALATED: ["DISMISSED_BY_HUMAN"],
  DISMISSED_BY_HUMAN: [],
};

// --- Response shapes ---

export interface Obligation {
  id: string;
  caseId: string;
  caseVersion: number;
  sequence: number;
  frequency: ObligationFrequency | string;
  dueDate: string;
  requirementText: string;
  createdAt: string;
}

export interface ObligationList {
  obligations: Obligation[];
  caseVersion: number;
}

export interface Observation {
  id: string;
  caseId: string;
  caseVersion: number;
  obligationId: string | null;
  observationType: string;
  body: string;
  // Three distinct timestamps: caller-supplied validity + observation instants,
  // and the DB clock's recording instant.
  effectiveAt: string;
  observedAt: string;
  recordedAt: string;
  evidenceRefs: string[];
}

export interface ObservationList {
  observations: Observation[];
  caseVersion: number;
}

export interface Alert {
  id: string;
  caseId: string;
  caseVersion: number;
  rule: AlertRule | string;
  status: AlertStatus | string;
  detail: string;
  sourceCovenantTestId: string | null;
  sourceObligationId: string | null;
  sourceObservationId: string | null;
  createdAt: string;
}

export interface AlertList {
  alerts: Alert[];
  caseVersion: number;
}

export interface RecordObservationResult {
  observation: Observation;
  // The OVERDUE_OBLIGATION alert this observation raised, or null if on time.
  alert: Alert | null;
}

export interface Covenant {
  id: string;
  caseId: string;
  caseVersion: number;
  name: string;
  metricKey: string;
  operator: ComparisonOperator | string;
  thresholdValue: string;
  thresholdVersion: number;
  createdAt: string;
}

export interface CovenantList {
  covenants: Covenant[];
  caseVersion: number;
}

export interface CovenantTest {
  id: string;
  covenantId: string;
  caseId: string;
  caseVersion: number;
  metricKey: string;
  operator: ComparisonOperator | string;
  numerator: string;
  denominator: string;
  thresholdValue: string;
  thresholdVersion: number;
  // The exact terms actually compared: lhs = numerator, rhs = threshold * denom.
  comparisonLhs: string;
  comparisonRhs: string;
  passed: boolean;
  recordedAt: string;
}

export interface RecordCovenantTestResult {
  test: CovenantTest;
  // The COVENANT_BREACH alert this test raised, or null if it passed.
  alert: Alert | null;
}

export interface CreateObligationsInput {
  frequency: ObligationFrequency;
  requirementText: string;
  fromDate: string;
  count: number;
}

export interface CreateObservationInput {
  observationType: string;
  body: string;
  effectiveAt: string;
  observedAt: string;
  obligationId?: string;
  evidenceRefs?: string[];
}

export interface CreateCovenantInput {
  name: string;
  metricKey: string;
  operator: ComparisonOperator;
  thresholdValue: string;
  thresholdVersion: number;
}

export interface RunCovenantTestInput {
  numerator: string;
  denominator?: string;
}

export interface DisposeAlertInput {
  toStatus: AlertStatus;
  rationale: string;
}

// --- Defensive parsing ---

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function strOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function bool(value: unknown): boolean {
  return value === true;
}

function strArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(str) : [];
}

function parseObligation(value: unknown): Obligation {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    sequence: num(raw.sequence),
    frequency: str(raw.frequency),
    dueDate: str(raw.dueDate),
    requirementText: str(raw.requirementText),
    createdAt: str(raw.createdAt),
  };
}

export function parseObligationList(value: unknown): ObligationList {
  const raw = asRecord(value);
  return {
    obligations: Array.isArray(raw.obligations) ? raw.obligations.map(parseObligation) : [],
    caseVersion: num(raw.caseVersion),
  };
}

function parseObservation(value: unknown): Observation {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    obligationId: strOrNull(raw.obligationId),
    observationType: str(raw.observationType),
    body: str(raw.body),
    effectiveAt: str(raw.effectiveAt),
    observedAt: str(raw.observedAt),
    recordedAt: str(raw.recordedAt),
    evidenceRefs: strArray(raw.evidenceRefs),
  };
}

export function parseObservationList(value: unknown): ObservationList {
  const raw = asRecord(value);
  return {
    observations: Array.isArray(raw.observations)
      ? raw.observations.map(parseObservation)
      : [],
    caseVersion: num(raw.caseVersion),
  };
}

function parseAlert(value: unknown): Alert {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    rule: str(raw.rule),
    status: str(raw.status),
    detail: str(raw.detail),
    sourceCovenantTestId: strOrNull(raw.sourceCovenantTestId),
    sourceObligationId: strOrNull(raw.sourceObligationId),
    sourceObservationId: strOrNull(raw.sourceObservationId),
    createdAt: str(raw.createdAt),
  };
}

function parseAlertOrNull(value: unknown): Alert | null {
  return value == null ? null : parseAlert(value);
}

export function parseAlertList(value: unknown): AlertList {
  const raw = asRecord(value);
  return {
    alerts: Array.isArray(raw.alerts) ? raw.alerts.map(parseAlert) : [],
    caseVersion: num(raw.caseVersion),
  };
}

export function parseRecordObservationResult(value: unknown): RecordObservationResult {
  const raw = asRecord(value);
  return {
    observation: parseObservation(raw.observation),
    alert: parseAlertOrNull(raw.alert),
  };
}

function parseCovenant(value: unknown): Covenant {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    name: str(raw.name),
    metricKey: str(raw.metricKey),
    operator: str(raw.operator),
    thresholdValue: str(raw.thresholdValue),
    thresholdVersion: num(raw.thresholdVersion),
    createdAt: str(raw.createdAt),
  };
}

export function parseCovenantList(value: unknown): CovenantList {
  const raw = asRecord(value);
  return {
    covenants: Array.isArray(raw.covenants) ? raw.covenants.map(parseCovenant) : [],
    caseVersion: num(raw.caseVersion),
  };
}

export function parseCovenantRecord(value: unknown): Covenant {
  return parseCovenant(value);
}

function parseCovenantTest(value: unknown): CovenantTest {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    covenantId: str(raw.covenantId),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    metricKey: str(raw.metricKey),
    operator: str(raw.operator),
    numerator: str(raw.numerator),
    denominator: str(raw.denominator),
    thresholdValue: str(raw.thresholdValue),
    thresholdVersion: num(raw.thresholdVersion),
    comparisonLhs: str(raw.comparisonLhs),
    comparisonRhs: str(raw.comparisonRhs),
    passed: bool(raw.passed),
    recordedAt: str(raw.recordedAt),
  };
}

export function parseRecordCovenantTestResult(value: unknown): RecordCovenantTestResult {
  const raw = asRecord(value);
  return {
    test: parseCovenantTest(raw.test),
    alert: parseAlertOrNull(raw.alert),
  };
}

export interface CovenantTestList {
  tests: CovenantTest[];
  caseVersion: number;
}

export function parseCovenantTestList(value: unknown): CovenantTestList {
  const raw = asRecord(value);
  return {
    tests: Array.isArray(raw.tests) ? raw.tests.map(parseCovenantTest) : [],
    caseVersion: num(raw.caseVersion),
  };
}

export function allowedAlertTransitions(status: string): readonly AlertStatus[] {
  return ALLOWED_ALERT_TRANSITIONS[status as AlertStatus] ?? [];
}

// Groups alerts by status in the fixed display order, dropping empty groups. Any
// alert whose status the UI does not recognize is NOT dropped — it falls into a
// trailing "UNKNOWN" bucket so it is still surfaced (fail closed), just with the
// unsupported label and no disposition edges.
export function groupAlertsByStatus(
  alerts: Alert[],
): { status: AlertStatus | "UNKNOWN"; alerts: Alert[] }[] {
  const known = new Set<string>(ALERT_STATUSES);
  const groups: { status: AlertStatus | "UNKNOWN"; alerts: Alert[] }[] = ALERT_STATUSES.map(
    (status) => ({
      status,
      alerts: alerts.filter((alert) => alert.status === status),
    }),
  ).filter((group) => group.alerts.length > 0);
  const unknown = alerts.filter((alert) => !known.has(String(alert.status)));
  if (unknown.length > 0) {
    groups.push({ status: "UNKNOWN", alerts: unknown });
  }
  return groups;
}

// --- Client ---

type Fetcher = typeof fetch;
type CsrfTokenProvider = () => string | null;

function readBrowserCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const index = part.indexOf("=");
    if (index < 0 || part.slice(0, index).trim() !== CSRF_COOKIE_NAME) continue;
    try {
      return decodeURIComponent(part.slice(index + 1).trim());
    } catch {
      return null;
    }
  }
  return null;
}

async function parseJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function parseApiError(
  body: unknown,
): { code: string; messageVi: string; retryable: boolean } | null {
  if (typeof body !== "object" || body === null) return null;
  const raw = body as Record<string, unknown>;
  if (typeof raw.code !== "string") return null;
  return {
    code: raw.code,
    messageVi: typeof raw.messageVi === "string" ? raw.messageVi : "",
    retryable: typeof raw.retryable === "boolean" ? raw.retryable : false,
  };
}

export class MonitoringApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  private caseBase(caseId: string): string {
    return `/api/v1/cases/${encodeURIComponent(caseId)}/monitoring`;
  }

  async listObligations(caseId: string): Promise<ObligationList> {
    return parseObligationList(await this.request(`${this.caseBase(caseId)}/obligations`));
  }

  async createObligations(
    caseId: string,
    input: CreateObligationsInput,
  ): Promise<ObligationList> {
    return parseObligationList(
      await this.request(`${this.caseBase(caseId)}/obligations`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    );
  }

  async listObservations(caseId: string): Promise<ObservationList> {
    return parseObservationList(await this.request(`${this.caseBase(caseId)}/observations`));
  }

  // Records ONE longitudinal observation; the OVERDUE_OBLIGATION rule may fire.
  async recordObservation(
    caseId: string,
    input: CreateObservationInput,
  ): Promise<RecordObservationResult> {
    return parseRecordObservationResult(
      await this.request(`${this.caseBase(caseId)}/observations`, {
        method: "POST",
        body: JSON.stringify(compact(input)),
      }),
    );
  }

  async listCovenants(caseId: string): Promise<CovenantList> {
    return parseCovenantList(await this.request(`${this.caseBase(caseId)}/covenants`));
  }

  async createCovenant(caseId: string, input: CreateCovenantInput): Promise<Covenant> {
    return parseCovenantRecord(
      await this.request(`${this.caseBase(caseId)}/covenants`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    );
  }

  // Tests supplied inputs against the covenant threshold; COVENANT_BREACH may fire.
  async runCovenantTest(
    caseId: string,
    covenantId: string,
    input: RunCovenantTestInput,
  ): Promise<RecordCovenantTestResult> {
    return parseRecordCovenantTestResult(
      await this.request(
        `${this.caseBase(caseId)}/covenants/${encodeURIComponent(covenantId)}/test`,
        { method: "POST", body: JSON.stringify(compact(input)) },
      ),
    );
  }

  async listCovenantTests(caseId: string): Promise<CovenantTestList> {
    return parseCovenantTestList(
      await this.request(`${this.caseBase(caseId)}/covenant-tests`),
    );
  }

  async listAlerts(caseId: string): Promise<AlertList> {
    return parseAlertList(await this.request(`${this.caseBase(caseId)}/alerts`));
  }

  // Human-only alert disposition along a validated lifecycle edge (rationale
  // required). 422 FORBIDDEN_ALERT_TRANSITION on a forbidden edge.
  async disposeAlert(
    caseId: string,
    alertId: string,
    input: DisposeAlertInput,
  ): Promise<Alert> {
    return parseAlert(
      await this.request(
        `${this.caseBase(caseId)}/alerts/${encodeURIComponent(alertId)}/disposition`,
        { method: "POST", body: JSON.stringify(input) },
      ),
    );
  }

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined) headers.set("Content-Type", "application/json");
    if (isMutation(init.method)) {
      const csrfToken = this.csrfTokenProvider();
      if (csrfToken) headers.set(CSRF_HEADER_NAME, csrfToken);
    }

    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      ...init,
      headers,
      credentials: "include",
      cache: "no-store",
    });
    const body = await parseJson(response);
    if (!response.ok) {
      const apiError = parseApiError(body);
      throw new ApiClientError(
        response.status,
        apiError?.code ?? "REQUEST_FAILED",
        apiError?.messageVi || "Yêu cầu không thành công.",
        apiError?.retryable ?? response.status >= 500,
      );
    }
    return body;
  }
}

// Drops undefined / empty-array optional fields so the BFF's exact-keys check
// never sees a key the officer did not fill in.
function compact(input: object): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    out[key] = value;
  }
  return out;
}

function isMutation(method: string | undefined): boolean {
  return method !== undefined && !["GET", "HEAD"].includes(method.toUpperCase());
}

export const monitoringApi = new MonitoringApiClient();

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

export function getMonitoringError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò được yêu cầu cho thao tác giám sát.";
      case 404:
        return "Không tìm thấy hồ sơ, nghĩa vụ, cam kết hoặc cảnh báo. Vui lòng tải lại.";
      case 409:
        return "Không thể hoàn tất: trạng thái đã thay đổi. Vui lòng tải lại.";
      case 422:
        return "Dữ liệu chưa hợp lệ hoặc chuyển trạng thái không được phép. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ giám sát sau cấp tín dụng chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display labels ---

export const OBLIGATION_FREQUENCY_LABELS: Record<ObligationFrequency, string> = {
  MONTHLY: "Hằng tháng",
  QUARTERLY: "Hằng quý",
};

export const COMPARISON_OPERATOR_LABELS: Record<ComparisonOperator, string> = {
  GTE: "≥ (lớn hơn hoặc bằng)",
  GT: "> (lớn hơn)",
  LTE: "≤ (nhỏ hơn hoặc bằng)",
  LT: "< (nhỏ hơn)",
  EQ: "= (bằng)",
};

// The compact operator symbol for the echoed arithmetic line.
export const COMPARISON_OPERATOR_SYMBOLS: Record<ComparisonOperator, string> = {
  GTE: "≥",
  GT: ">",
  LTE: "≤",
  LT: "<",
  EQ: "=",
};

export const ALERT_RULE_LABELS: Record<AlertRule, string> = {
  COVENANT_BREACH: "Vi phạm cam kết",
  OVERDUE_OBLIGATION: "Nghĩa vụ quá hạn",
};

export const ALERT_STATUS_LABELS: Record<AlertStatus, string> = {
  OPEN: "Đang mở",
  ACKNOWLEDGED: "Đã tiếp nhận",
  ESCALATED: "Đã chuyển cấp",
  DISMISSED_BY_HUMAN: "Đã đóng (thẩm quyền)",
};

// Precise per-target disposition labels (never a generic verb).
export const ALERT_TRANSITION_LABELS: Record<AlertStatus, string> = {
  OPEN: "Mở lại cảnh báo",
  ACKNOWLEDGED: "Tiếp nhận cảnh báo",
  ESCALATED: "Chuyển cấp cảnh báo",
  DISMISSED_BY_HUMAN: "Đóng cảnh báo (ghi thẩm quyền)",
};

export const UNSUPPORTED_ENUM_LABEL = "Trạng thái chưa được hỗ trợ";

export function labelOrUnsupported<K extends string>(
  map: Record<K, string>,
  key: string,
): string {
  return (map as Record<string, string>)[key] ?? UNSUPPORTED_ENUM_LABEL;
}

export function shortId(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso || "—";
  return date.toLocaleString("vi-VN");
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("vi-VN");
}
