// Self-contained API bindings for the stage-13 RepaymentLedger screen
// ("Thu nợ gốc, lãi và phí"). Mirrors lib/api/client.ts conventions but shares
// no mutable state with it.
//
// Backend truth mirrored here: services/api/src/creditops/api/repayments.py and
// services/.../domain/repayments.py.
//   POST /api/v1/cases/{caseId}/repayments                          -> 201 Facility (bound to permitting decision)
//   POST /api/v1/cases/{caseId}/repayments/{facilityId}/events      -> Event (201 created, or 200 idempotent duplicate)
//   GET  /api/v1/cases/{caseId}/repayments/{facilityId}/ledger      -> Ledger (recomputed snapshot + exceptions + notes)
//   POST /api/v1/cases/{caseId}/repayments/{facilityId}/notes       -> 201 CollectionNote (human free-text proposal)
//
// CONTRACT NOTE: there is no facility/events READ endpoint. The ledger recompute
// carries periods, exceptions and notes but NOT the facility summary or the raw
// event list, so this client can only surface a facility opened / events
// appended in the current session. All money figures are the server's exact
// Decimal strings and are rendered VERBATIM — never reformatted client-side.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/repayments.py) ---

export type EventKind = "PAYMENT" | "REVERSAL";

export type PeriodStatus = "PAID" | "PARTIALLY_PAID" | "UNPAID";

export type CollectionsExceptionKind =
  | "OVERDUE_INSTALLMENT"
  | "UNDERPAID_PERIOD"
  | "UNMATCHED_PAYMENT";

export type NoteKind = "OBSERVATION" | "PROPOSED_ACTION";

// mirrors application/underwriting/calculators.py RepaymentStyle
export type RepaymentStyle = "EQUAL_PRINCIPAL" | "BALLOON";

// The deterministic collections-exception surface, in a stable display order so
// grouped rendering is reproducible.
export const EXCEPTION_KIND_ORDER: readonly CollectionsExceptionKind[] = [
  "OVERDUE_INSTALLMENT",
  "UNDERPAID_PERIOD",
  "UNMATCHED_PAYMENT",
];

// --- Response shapes ---

export interface Facility {
  id: string;
  caseId: string;
  caseVersion: number;
  decisionId: string;
  principal: string;
  annualRatePercent: string;
  termMonths: number;
  periodicFee: string;
  repaymentStyle: RepaymentStyle | string;
  firstPaymentDate: string;
}

export interface RepaymentEvent {
  id: string;
  facilityId: string;
  kind: EventKind | string;
  amount: string;
  externalReference: string;
  reversedEventId: string | null;
  effectiveDate: string;
  // The 201-vs-200 idempotency distinction: false when a duplicate delivery
  // returned the EXISTING row.
  created: boolean;
}

export interface LedgerPeriod {
  period: number;
  dueDate: string;
  expectedFee: string;
  expectedInterest: string;
  expectedPrincipal: string;
  allocatedFee: string;
  allocatedInterest: string;
  allocatedPrincipal: string;
  outstandingTotal: string;
  status: PeriodStatus | string;
  overdue: boolean;
}

export interface CollectionsException {
  kind: CollectionsExceptionKind | string;
  period: number | null;
  amount: string;
  detailVi: string;
}

export interface CollectionNote {
  id: string;
  noteKind: NoteKind | string;
  noteText: string;
  proposedAction: string | null;
  authorRole: string;
}

export interface LedgerSnapshot {
  facilityId: string;
  asOf: string;
  allocationPolicyVersion: string;
  netPaid: string;
  outstandingFees: string;
  outstandingInterest: string;
  outstandingPrincipal: string;
  outstandingTotal: string;
  overpayment: string;
  isSettled: boolean;
  periods: LedgerPeriod[];
  exceptions: CollectionsException[];
  notes: CollectionNote[];
}

// --- Request inputs ---

export interface CreateFacilityInput {
  principal: string;
  annualRatePercent: string;
  termMonths: number;
  repaymentStyle: RepaymentStyle;
  firstPaymentDate: string;
  periodicFee?: string;
}

export interface RecordEventInput {
  kind: EventKind;
  amount: string;
  externalReference: string;
  effectiveDate: string;
  reversedEventId?: string;
}

export interface CreateNoteInput {
  noteKind: NoteKind;
  noteText: string;
  proposedAction?: string;
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

function numOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bool(value: unknown): boolean {
  return value === true;
}

export function parseFacility(value: unknown): Facility {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    decisionId: str(raw.decisionId),
    principal: str(raw.principal),
    annualRatePercent: str(raw.annualRatePercent),
    termMonths: num(raw.termMonths),
    periodicFee: str(raw.periodicFee),
    repaymentStyle: str(raw.repaymentStyle),
    firstPaymentDate: str(raw.firstPaymentDate),
  };
}

export function parseRepaymentEvent(value: unknown): RepaymentEvent {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    facilityId: str(raw.facilityId),
    kind: str(raw.kind),
    amount: str(raw.amount),
    externalReference: str(raw.externalReference),
    reversedEventId: strOrNull(raw.reversedEventId),
    effectiveDate: str(raw.effectiveDate),
    created: bool(raw.created),
  };
}

function parsePeriod(value: unknown): LedgerPeriod {
  const raw = asRecord(value);
  return {
    period: num(raw.period),
    dueDate: str(raw.dueDate),
    expectedFee: str(raw.expectedFee),
    expectedInterest: str(raw.expectedInterest),
    expectedPrincipal: str(raw.expectedPrincipal),
    allocatedFee: str(raw.allocatedFee),
    allocatedInterest: str(raw.allocatedInterest),
    allocatedPrincipal: str(raw.allocatedPrincipal),
    outstandingTotal: str(raw.outstandingTotal),
    status: str(raw.status),
    overdue: bool(raw.overdue),
  };
}

function parseException(value: unknown): CollectionsException {
  const raw = asRecord(value);
  return {
    kind: str(raw.kind),
    period: numOrNull(raw.period),
    amount: str(raw.amount),
    detailVi: str(raw.detailVi),
  };
}

function parseNote(value: unknown): CollectionNote {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    noteKind: str(raw.noteKind),
    noteText: str(raw.noteText),
    proposedAction: strOrNull(raw.proposedAction),
    authorRole: str(raw.authorRole),
  };
}

export function parseLedgerSnapshot(value: unknown): LedgerSnapshot {
  const raw = asRecord(value);
  return {
    facilityId: str(raw.facilityId),
    asOf: str(raw.asOf),
    allocationPolicyVersion: str(raw.allocationPolicyVersion),
    netPaid: str(raw.netPaid),
    outstandingFees: str(raw.outstandingFees),
    outstandingInterest: str(raw.outstandingInterest),
    outstandingPrincipal: str(raw.outstandingPrincipal),
    outstandingTotal: str(raw.outstandingTotal),
    overpayment: str(raw.overpayment),
    isSettled: bool(raw.isSettled),
    periods: Array.isArray(raw.periods) ? raw.periods.map(parsePeriod) : [],
    exceptions: Array.isArray(raw.exceptions) ? raw.exceptions.map(parseException) : [],
    notes: Array.isArray(raw.notes) ? raw.notes.map(parseNote) : [],
  };
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

function parseErrorDetails(body: unknown): Record<string, unknown> | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  const details = (body as Record<string, unknown>).details;
  if (typeof details !== "object" || details === null || Array.isArray(details)) return null;
  return details as Record<string, unknown>;
}

export class RepaymentsApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // Opens ONE disbursed facility bound to a permitting decision. 409
  // FACILITY_REQUIRES_APPROVAL_DECISION when no approval exists yet.
  async createFacility(caseId: string, input: CreateFacilityInput): Promise<Facility> {
    return parseFacility(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/repayments`, {
        method: "POST",
        body: JSON.stringify(compact(input)),
      }),
    );
  }

  // Appends ONE payment / reversal. Idempotent on externalReference: a duplicate
  // returns the EXISTING row with created=false (200); a new event is created=true
  // (201). The 200-vs-201 distinction is surfaced via the `created` flag.
  async recordEvent(
    caseId: string,
    facilityId: string,
    input: RecordEventInput,
  ): Promise<RepaymentEvent> {
    return parseRepaymentEvent(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/repayments/${encodeURIComponent(
          facilityId,
        )}/events`,
        { method: "POST", body: JSON.stringify(compact(input)) },
      ),
    );
  }

  // Recomputes and returns the ledger snapshot + collections exceptions + notes.
  async getLedger(
    caseId: string,
    facilityId: string,
    asOf?: string,
  ): Promise<LedgerSnapshot> {
    const suffix = asOf ? `?asOf=${encodeURIComponent(asOf)}` : "";
    return parseLedgerSnapshot(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/repayments/${encodeURIComponent(
          facilityId,
        )}/ledger${suffix}`,
      ),
    );
  }

  // Records ONE human free-text observation / proposed action. Nothing executes.
  async createNote(
    caseId: string,
    facilityId: string,
    input: CreateNoteInput,
  ): Promise<CollectionNote> {
    return parseNote(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/repayments/${encodeURIComponent(
          facilityId,
        )}/notes`,
        { method: "POST", body: JSON.stringify(compact(input)) },
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
        null,
        parseErrorDetails(body),
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

export const repaymentsApi = new RepaymentsApiClient();

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

export function getRepaymentError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò được yêu cầu cho thao tác thu nợ.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc khoản vay. Vui lòng tải lại.";
      case 409:
        return "Không thể hoàn tất: trạng thái đã thay đổi hoặc chưa đủ điều kiện. Vui lòng tải lại.";
      case 422:
        return "Dữ liệu chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ sổ thu nợ chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display labels (fail closed on unknown enums) ---

export const EVENT_KIND_LABELS: Record<EventKind, string> = {
  PAYMENT: "Thanh toán",
  REVERSAL: "Bút toán đảo",
};

export const PERIOD_STATUS_LABELS: Record<PeriodStatus, string> = {
  PAID: "Đã trả đủ",
  PARTIALLY_PAID: "Trả một phần",
  UNPAID: "Chưa trả",
};

export const EXCEPTION_KIND_LABELS: Record<CollectionsExceptionKind, string> = {
  OVERDUE_INSTALLMENT: "Kỳ trả nợ quá hạn",
  UNDERPAID_PERIOD: "Kỳ trả nợ thiếu",
  UNMATCHED_PAYMENT: "Khoản thu chưa khớp",
};

export const NOTE_KIND_LABELS: Record<NoteKind, string> = {
  OBSERVATION: "Quan sát",
  PROPOSED_ACTION: "Đề xuất hành động",
};

export const REPAYMENT_STYLE_LABELS: Record<RepaymentStyle, string> = {
  EQUAL_PRINCIPAL: "Trả gốc đều",
  BALLOON: "Trả gốc cuối kỳ (balloon)",
};

export const UNSUPPORTED_ENUM_LABEL = "Loại chưa được hỗ trợ";

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

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("vi-VN");
}
