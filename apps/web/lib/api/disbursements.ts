// Self-contained API bindings for the stage-11 proposed-disbursement workspace
// ("Giải ngân vốn vay"). Mirrors lib/api/client.ts conventions (BFF base
// "/api/creditops", the "__Host-creditops-csrf" cookie surfaced as the
// "x-creditops-csrf" header on mutations, ApiClientError-style typed failures)
// but shares no mutable state with it — the gap-requests.ts self-contained
// pattern.
//
// Backend truth mirrored here: services/api/src/creditops/api/disbursements.py
// and services/.../domain/disbursements.py.
//   GET  /api/v1/cases/{caseId}/proposed-disbursements                  -> DisbursementList
//   POST /api/v1/cases/{caseId}/proposed-disbursements                  -> 201/200 Action
//   POST /api/v1/cases/{caseId}/proposed-disbursements/{id}/validate    -> gate 1 (HG_DISBURSEMENT_VALIDATED)
//   POST /api/v1/cases/{caseId}/proposed-disbursements/{id}/authorize   -> gate 2 (HG_DISBURSEMENT_AUTHORIZED, different actor)
//   POST /api/v1/cases/{caseId}/proposed-disbursements/{id}/execute     -> labelled-mock run (both gates + different-from-creator)
//   POST /api/v1/cases/{caseId}/proposed-disbursements/{id}/reconcile   -> human resolution of an unresolved execution
//
// Credit Operations only PREPARES the action; nothing executes against a real
// core-banking system. Execution runs a labelled deterministic MOCK after TWO
// separate human gates satisfied by DIFFERENT actors. An EXECUTION_UNKNOWN result
// is NEVER blindly retried — only a human reconciliation resolves it.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/disbursements.py) ---

export type GateStatus = "OPEN" | "SATISFIED";

// The CLOSED execution-lifecycle set.
export type ExecutionStatus =
  | "PROPOSED"
  | "EXECUTION_REQUESTED"
  | "EXECUTION_UNKNOWN"
  | "CONFIRMED_EXECUTED"
  | "CONFIRMED_NOT_EXECUTED";

// The two outcomes a human reconciliation may record for an unresolved execution.
export type ReconciliationOutcome = "CONFIRMED_EXECUTED" | "CONFIRMED_NOT_EXECUTED";

export const RECONCILIATION_OUTCOMES: readonly ReconciliationOutcome[] = [
  "CONFIRMED_EXECUTED",
  "CONFIRMED_NOT_EXECUTED",
];

// The fixed label of the ONLY execution adapter this project wires (a mock).
export const MOCK_ADAPTER_LABEL = "MOCK_DISBURSEMENT_EXECUTION_ADAPTER";

// --- Response shapes ---

export interface DisbursementAction {
  id: string;
  caseId: string;
  caseVersion: number;
  decisionId: string;
  amount: string;
  currency: string;
  beneficiaryRef: string;
  accountRef: string;
  status: ExecutionStatus | string;
  createdBy: string;
  createdAt: string;
}

export interface ExecutionReceipt {
  id: string;
  actionId: string;
  idempotencyKey: string;
  adapterLabel: string;
  resultStatus: ExecutionStatus | string;
  receiptRef: string | null;
  recordedBy: string;
  createdAt: string;
}

export interface DisbursementActionDetail {
  action: DisbursementAction;
  receipts: ExecutionReceipt[];
  validatedGateStatus: GateStatus | string;
  authorizedGateStatus: GateStatus | string;
}

export interface DisbursementList {
  actions: DisbursementActionDetail[];
  caseVersion: number;
}

export interface CreateDisbursementInput {
  beneficiaryRef: string;
  accountRef: string;
  amount?: string;
  currency?: string;
}

export interface ReconcileInput {
  outcome: ReconciliationOutcome;
  rationale: string;
}

// --- Defensive parsing (the payload crosses a proxy; never trust its shape) ---

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

function parseAction(value: unknown): DisbursementAction {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    decisionId: str(raw.decisionId),
    amount: str(raw.amount),
    currency: str(raw.currency),
    beneficiaryRef: str(raw.beneficiaryRef),
    accountRef: str(raw.accountRef),
    status: str(raw.status),
    createdBy: str(raw.createdBy),
    createdAt: str(raw.createdAt),
  };
}

function parseReceipt(value: unknown): ExecutionReceipt {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    actionId: str(raw.actionId),
    idempotencyKey: str(raw.idempotencyKey),
    adapterLabel: str(raw.adapterLabel),
    resultStatus: str(raw.resultStatus),
    receiptRef: strOrNull(raw.receiptRef),
    recordedBy: str(raw.recordedBy),
    createdAt: str(raw.createdAt),
  };
}

function parseDetail(value: unknown): DisbursementActionDetail {
  const raw = asRecord(value);
  return {
    action: parseAction(raw.action),
    receipts: Array.isArray(raw.receipts) ? raw.receipts.map(parseReceipt) : [],
    validatedGateStatus: str(raw.validatedGateStatus),
    authorizedGateStatus: str(raw.authorizedGateStatus),
  };
}

export function parseDisbursementList(value: unknown): DisbursementList {
  const raw = asRecord(value);
  return {
    actions: Array.isArray(raw.actions) ? raw.actions.map(parseDetail) : [],
    caseVersion: num(raw.caseVersion),
  };
}

export function parseDisbursementAction(value: unknown): DisbursementAction {
  return parseAction(value);
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

export class DisbursementsApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async list(caseId: string): Promise<DisbursementList> {
    return parseDisbursementList(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/proposed-disbursements`,
      ),
    );
  }

  // The maker opens ONE proposed action derived from approved terms. 409 when no
  // approval decision / conditions gate exists; 422 on a currency / amount /
  // over-cap mismatch.
  async create(
    caseId: string,
    input: CreateDisbursementInput,
  ): Promise<DisbursementAction> {
    return parseDisbursementAction(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/proposed-disbursements`,
        { method: "POST", body: JSON.stringify(compact(input)) },
      ),
    );
  }

  // Gate 1 (HG_DISBURSEMENT_VALIDATED) — empty body.
  async validate(caseId: string, actionId: string): Promise<unknown> {
    return this.gateWrite(caseId, actionId, "validate");
  }

  // Gate 2 (HG_DISBURSEMENT_AUTHORIZED) — empty body. 409 VALIDATION_REQUIRED
  // before gate 1; 409 SAME_ACTOR_FORBIDDEN if the authorizer validated.
  async authorize(caseId: string, actionId: string): Promise<unknown> {
    return this.gateWrite(caseId, actionId, "authorize");
  }

  // Run the labelled mock adapter — empty body. 409 DISBURSEMENT_NOT_AUTHORIZED
  // without both gates; 409 SAME_ACTOR_FORBIDDEN if the executor created it; 409
  // RECONCILIATION_REQUIRED / ALREADY_EXECUTED on a resolved / stranded action.
  async execute(caseId: string, actionId: string): Promise<unknown> {
    return this.gateWrite(caseId, actionId, "execute");
  }

  private async gateWrite(
    caseId: string,
    actionId: string,
    verb: "validate" | "authorize" | "execute",
  ): Promise<unknown> {
    return this.request(
      `/api/v1/cases/${encodeURIComponent(caseId)}/proposed-disbursements/${encodeURIComponent(
        actionId,
      )}/${verb}`,
      { method: "POST", body: "{}" },
    );
  }

  // Human resolution of an unresolved execution (mandatory rationale). Only a
  // CONFIRMED_NOT_EXECUTED outcome re-opens a NEW attempt.
  async reconcile(
    caseId: string,
    actionId: string,
    input: ReconcileInput,
  ): Promise<DisbursementAction> {
    return parseDisbursementAction(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/proposed-disbursements/${encodeURIComponent(
          actionId,
        )}/reconcile`,
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

// Drops undefined optional fields so the BFF's exact-keys check never sees a key
// the officer did not fill in.
function compact(input: object): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined) continue;
    out[key] = value;
  }
  return out;
}

function isMutation(method: string | undefined): boolean {
  return method !== undefined && !["GET", "HEAD"].includes(method.toUpperCase());
}

export const disbursementsApi = new DisbursementsApiClient();

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

// Names what failed and how to recover. Prefers the server's own specific
// Vietnamese message; otherwise maps each error CODE (then status) to a distinct
// message, so a 409 SAME_ACTOR_FORBIDDEN never reads like a 409 ALREADY_EXECUTED.
export function getDisbursementError(error: unknown): string {
  if (error instanceof ApiClientError) {
    const byCode = DISBURSEMENT_ERROR_CODE_MESSAGES[error.code];
    if (byCode) return byCode;
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò được yêu cầu cho thao tác giải ngân.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc hành động giải ngân. Vui lòng tải lại.";
      case 409:
        return "Không thể hoàn tất: trạng thái đã thay đổi. Vui lòng tải lại để xem bản mới nhất.";
      case 422:
        return "Dữ liệu chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ giải ngân chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// Each backend error code maps to ONE distinct Vietnamese message (design giai
// đoạn 11). The server also sends its own messageVi; these guarantee a specific
// message even when the proxy strips it.
export const DISBURSEMENT_ERROR_CODE_MESSAGES: Record<string, string> = {
  DISBURSEMENT_REQUIRES_APPROVAL_DECISION:
    "Chưa có quyết định phê duyệt tín dụng cho phiên bản hồ sơ hiện tại để đề xuất giải ngân.",
  DISBURSEMENT_CONDITIONS_NOT_CONFIRMED:
    "Chưa xác nhận điều kiện giải ngân (HG_DISBURSEMENT_CONDITIONS_CONFIRMED) cho phiên bản này.",
  VALIDATION_REQUIRED:
    "Chưa thể uỷ quyền: cổng kiểm tra giải ngân (HG_DISBURSEMENT_VALIDATED) chưa được thỏa mãn trước.",
  SAME_ACTOR_FORBIDDEN:
    "Tách biệt nhiệm vụ: người thực hiện bước này phải khác với người đã thực hiện bước trước.",
  DISBURSEMENT_NOT_AUTHORIZED:
    "Chưa thể thực thi: cần cả hai cổng kiểm tra và uỷ quyền được thỏa mãn.",
  RECONCILIATION_REQUIRED:
    "Lần thực thi trước chưa xác định (EXECUTION_UNKNOWN); cần đối soát thủ công, không tự động thực thi lại.",
  ALREADY_EXECUTED: "Hành động giải ngân đã được xác nhận thực thi.",
  NOT_RECONCILABLE:
    "Hành động không ở trạng thái cần đối soát (chỉ EXECUTION_REQUESTED / EXECUTION_UNKNOWN).",
  CURRENCY_MISMATCH: "Loại tiền giải ngân khác với loại tiền đã được phê duyệt.",
  AMOUNT_EXCEEDS_APPROVED: "Số tiền giải ngân vượt quá số tiền đã được phê duyệt.",
  INVALID_AMOUNT: "Số tiền giải ngân không phải số thập phân hợp lệ (> 0).",
  INVALID_OUTCOME:
    "Kết quả đối soát phải là CONFIRMED_EXECUTED hoặc CONFIRMED_NOT_EXECUTED.",
  DISBURSEMENT_ACTION_NOT_FOUND:
    "Không tìm thấy hành động giải ngân trong phiên bản hồ sơ này.",
};

// --- Display label maps (Vietnamese, sentence case, plain verbs) ---

export const GATE_STATUS_LABELS: Record<GateStatus, string> = {
  OPEN: "Đang chờ",
  SATISFIED: "Đạt",
};

export const EXECUTION_STATUS_LABELS: Record<ExecutionStatus, string> = {
  PROPOSED: "Đã đề xuất",
  EXECUTION_REQUESTED: "Đã yêu cầu thực thi (chưa rõ kết quả)",
  EXECUTION_UNKNOWN: "Kết quả chưa xác định — cần đối soát",
  CONFIRMED_EXECUTED: "Đã xác nhận thực thi",
  CONFIRMED_NOT_EXECUTED: "Đã xác nhận không thực thi",
};

export const RECONCILIATION_OUTCOME_LABELS: Record<ReconciliationOutcome, string> = {
  CONFIRMED_EXECUTED: "Đã thực thi (tiền đã chuyển)",
  CONFIRMED_NOT_EXECUTED: "Không thực thi (tiền chưa chuyển)",
};

// The two unresolved states whose only forward move is a human reconciliation —
// never a blind retry. Rendered as a distinct blocking state in the workspace.
export function isUnresolvedExecution(status: string): boolean {
  return status === "EXECUTION_UNKNOWN" || status === "EXECUTION_REQUESTED";
}

// Fail closed on any enum value the UI does not recognize: render a neutral
// "unsupported" label rather than leak a raw backend token or crash.
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

// Money is an exact-decimal STRING carried verbatim from the backend; render the
// exact digits with the currency, never reparsing through a float.
export function formatAmount(amount: string, currency: string): string {
  const text = amount && amount.length > 0 ? amount : "—";
  return currency ? `${text} ${currency}` : text;
}
