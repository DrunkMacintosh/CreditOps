// Self-contained API bindings for the pre-Risk Evidence-Gap request batch
// ("Danh sách yêu cầu bổ sung") surface on the Khoảng trống screen. Kept
// separate from lib/api/client.ts on purpose; it mirrors that client's
// conventions — BFF base "/api/creditops", the "__Host-creditops-csrf" cookie
// surfaced as the "x-creditops-csrf" header on mutations, and
// ApiClientError-style typed failures — so the two never share mutable state.
//
// Backend truth mirrored here: services/api/src/creditops/api/gap_requests.py.
//   GET  /api/v1/cases/{caseId}/gap-request-batches
//        -> GapRequestBatchStatus (batch + dispositions + staleness + gate)
//   POST /api/v1/cases/{caseId}/gap-request-batches
//        -> 200/201 GapRequestBatch (assemble-or-get; idempotent on the open-gap hash)
//   POST /api/v1/cases/{caseId}/gap-request-batches/{batchId}/disposition
//        -> 201 RecordDispositionResult (append-only human disposition; re-derives G2)
//
// The intake officer RECORDS one disposition; the system never sends an outbound
// request anywhere. A REJECTED disposition, a stale batch, or a stale case
// version never satisfies the gate — the server re-derives; this client only
// records and reads.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/gap_request_batches.py) ---

export type GateStatus = "OPEN" | "SATISFIED";

export type GapBlockingLevel = "BLOCKING" | "CONDITIONAL" | "CLARIFICATION";

export type BatchDispositionType =
  | "APPROVED_ALL"
  | "APPROVED_WITH_CHANGES"
  | "REJECTED"
  | "NO_OUTBOUND_REQUESTS";

export type ItemDisposition = "APPROVED" | "REMOVED" | "EDITED";

// --- Response shapes ---

export interface GapRequestItem {
  id: string;
  gapId: string;
  requestText: string;
  blockingLevel: GapBlockingLevel | string;
}

export interface GapRequestBatch {
  batchId: string;
  caseId: string;
  caseVersion: number;
  openGapSnapshotHash: string;
  items: GapRequestItem[];
}

export interface BatchDisposition {
  id: string;
  batchId: string;
  dispositionType: BatchDispositionType | string;
  itemDispositions: Record<string, ItemDisposition | string>;
  editedTexts: Record<string, string>;
  actorId: string;
  actorRole: string;
  rationale: string;
  createdAt: string;
}

export interface GapRequestBatchStatus {
  batch: GapRequestBatch;
  stale: boolean;
  currentOpenGapHash: string;
  dispositions: BatchDisposition[];
  gateStatus: GateStatus | string;
}

export interface RecordDispositionResult {
  disposition: BatchDisposition;
  stale: boolean;
  gateStatus: GateStatus | string;
}

// The exact request body the disposition endpoint accepts. itemDispositions and
// editedTexts are omitted for REJECTED / NO_OUTBOUND_REQUESTS / APPROVED_ALL.
export interface RecordDispositionInput {
  dispositionType: BatchDispositionType;
  rationale: string;
  itemDispositions?: Record<string, ItemDisposition>;
  editedTexts?: Record<string, string>;
}

// --- Defensive parsing (the payload crosses a proxy; never trust its shape) ---

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string {
  return typeof value === "string"
    ? value
    : value === undefined || value === null
      ? ""
      : String(value);
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function bool(value: unknown): boolean {
  return value === true;
}

function parseStringMap(value: unknown): Record<string, string> {
  const raw = asRecord(value);
  const result: Record<string, string> = {};
  for (const [key, entry] of Object.entries(raw)) {
    result[key] = str(entry);
  }
  return result;
}

function parseItem(value: unknown): GapRequestItem {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    gapId: str(raw.gapId),
    requestText: str(raw.requestText),
    blockingLevel: str(raw.blockingLevel),
  };
}

function parseBatch(value: unknown): GapRequestBatch {
  const raw = asRecord(value);
  return {
    batchId: str(raw.batchId),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    openGapSnapshotHash: str(raw.openGapSnapshotHash),
    items: Array.isArray(raw.items) ? raw.items.map(parseItem) : [],
  };
}

function parseDisposition(value: unknown): BatchDisposition {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    batchId: str(raw.batchId),
    dispositionType: str(raw.dispositionType),
    itemDispositions: parseStringMap(raw.itemDispositions),
    editedTexts: parseStringMap(raw.editedTexts),
    actorId: str(raw.actorId),
    actorRole: str(raw.actorRole),
    rationale: str(raw.rationale),
    createdAt: str(raw.createdAt),
  };
}

export function parseGapRequestBatchStatus(value: unknown): GapRequestBatchStatus {
  const raw = asRecord(value);
  return {
    batch: parseBatch(raw.batch),
    stale: bool(raw.stale),
    currentOpenGapHash: str(raw.currentOpenGapHash),
    dispositions: Array.isArray(raw.dispositions)
      ? raw.dispositions.map(parseDisposition)
      : [],
    gateStatus: str(raw.gateStatus),
  };
}

export function parseGapRequestBatch(value: unknown): GapRequestBatch {
  return parseBatch(value);
}

export function parseRecordDispositionResult(value: unknown): RecordDispositionResult {
  const raw = asRecord(value);
  return {
    disposition: parseDisposition(raw.disposition),
    stale: bool(raw.stale),
    gateStatus: str(raw.gateStatus),
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

export class GapRequestsApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // Reads the current batch, its human dispositions, the staleness flag, and
  // the derived gate status. 404 GAP_REQUEST_BATCH_NOT_AVAILABLE means no batch
  // has been assembled for this case version yet — an empty state, not an error.
  async getBatch(caseId: string): Promise<GapRequestBatchStatus> {
    return parseGapRequestBatchStatus(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/gap-request-batches`,
      ),
    );
  }

  // Assemble-or-get: snapshots the CURRENT open gaps and deterministically
  // builds a versioned batch. Idempotent on the open-gap hash. Only ever
  // invoked by explicit officer action — never on render.
  async assembleBatch(caseId: string): Promise<GapRequestBatch> {
    return parseGapRequestBatch(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/gap-request-batches`,
        { method: "POST", body: "{}" },
      ),
    );
  }

  // Records ONE append-only human disposition for ONE batch; the server then
  // re-derives G2 against the CURRENT open gaps and case version.
  async recordDisposition(
    caseId: string,
    batchId: string,
    input: RecordDispositionInput,
  ): Promise<RecordDispositionResult> {
    return parseRecordDispositionResult(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/gap-request-batches/${encodeURIComponent(
          batchId,
        )}/disposition`,
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

function isMutation(method: string | undefined): boolean {
  return method !== undefined && !["GET", "HEAD"].includes(method.toUpperCase());
}

export const gapRequestsApi = new GapRequestsApiClient();

// The GET returns 404 with this code when no batch has been assembled for the
// case version yet — an empty state (assemble on explicit action), not an error.
export function isGapRequestBatchNotAvailable(error: unknown): boolean {
  return (
    error instanceof ApiClientError && error.code === "GAP_REQUEST_BATCH_NOT_AVAILABLE"
  );
}

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

// Names what failed and how to recover. Prefers the server's own specific
// Vietnamese message; otherwise maps by status.
export function getGapRequestError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò cán bộ tiếp nhận để duyệt yêu cầu bổ sung.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc đợt yêu cầu bổ sung. Vui lòng tải lại.";
      case 409:
        return "Dữ liệu đã thay đổi. Vui lòng tải lại để xem bản mới nhất.";
      case 422:
        return "Thông tin quyết định chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ yêu cầu bổ sung chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display label maps (Vietnamese, sentence case, plain verbs) ---

export const BATCH_DISPOSITION_TYPE_LABELS: Record<BatchDispositionType, string> = {
  APPROVED_ALL: "Duyệt toàn bộ yêu cầu",
  APPROVED_WITH_CHANGES: "Duyệt kèm chỉnh sửa từng mục",
  REJECTED: "Từ chối đợt yêu cầu",
  NO_OUTBOUND_REQUESTS: "Không phát sinh yêu cầu gửi ra",
};

export const ITEM_DISPOSITION_LABELS: Record<ItemDisposition, string> = {
  APPROVED: "Giữ nguyên",
  REMOVED: "Loại bỏ",
  EDITED: "Chỉnh sửa nội dung",
};

export const BLOCKING_LEVEL_LABELS: Record<GapBlockingLevel, string> = {
  BLOCKING: "Chặn",
  CONDITIONAL: "Có điều kiện",
  CLARIFICATION: "Cần làm rõ",
};

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
