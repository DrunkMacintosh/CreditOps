import type {
  AuditEventListDto,
  CompleteUploadResponseDto,
  ConfirmDocumentRequestDto,
  ConflictListDto,
  CreateCaseRequestDto,
  CreateUploadIntentRequestDto,
  CreditCaseDto,
  CreditCaseListDto,
  CreditOpsApi,
  DocumentReviewDto,
  EvidenceListDto,
  HandoffDto,
  IntakeCompletionResultDto,
  UploadIntentDto,
} from "./contracts";
import {
  parseApiError,
  parseAuditEventList,
  parseCompleteUpload,
  parseConflictList,
  parseCreditCase,
  parseCreditCaseList,
  parseDocumentReview,
  parseEvidenceList,
  parseHandoff,
  parseIntakeCompletion,
  parseUploadIntent,
} from "./schemas";

type Fetcher = typeof fetch;
type CsrfTokenProvider = () => string | null;

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly retryable: boolean,
    public readonly retryAfterSeconds: number | null = null,
    // Structured machine-readable context forwarded verbatim from the API error
    // body (e.g. 409 INTAKE_INCOMPLETE carries {reasons, unresolvedCount}).
    public readonly details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

// The unresolved-completeness reasons carried by a 409 INTAKE_INCOMPLETE, or
// null when the error is anything else. Reasons are the domain validator's own
// verdict; render them as the unresolved-items list.
export function getIntakeIncompleteReasons(error: unknown): string[] | null {
  if (!(error instanceof ApiClientError) || error.code !== "INTAKE_INCOMPLETE") {
    return null;
  }
  const reasons = error.details?.reasons;
  if (!Array.isArray(reasons)) return [];
  return reasons.filter((reason): reason is string => typeof reason === "string");
}

export function getVietnameseApiError(error: unknown): string {
  if (error instanceof ApiClientError || isDirectStorageError(error)) {
    if (error.code === "UPLOAD_INTENT_EXPIRED") {
      return "Phiên tải lên đã hết hạn. Vui lòng thử lại.";
    }
    if (error.code === "STALE_DOCUMENT_VERSION") {
      return "Phiên bản tài liệu đã thay đổi. Bản nháp của bạn được giữ nguyên; vui lòng tải lại để xem phiên bản mới.";
    }
    switch (error.status) {
      case 401:
        // Judges are anonymous demo actors, not accounts — there is nothing
        // to "log back into"; the only recovery is minting a fresh demo
        // session from the landing page.
        return "Phiên demo đã hết hạn. Vui lòng quay lại trang chủ và bắt đầu lại demo.";
      case 403:
        return "Bạn không có quyền thực hiện thao tác này trên hồ sơ.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.";
      case 409:
        return "Dữ liệu đã thay đổi hoặc thao tác bị trùng. Vui lòng tải lại.";
      case 413:
        return "Tài liệu vượt quá dung lượng được phép.";
      case 415:
        return "Định dạng tài liệu chưa được hỗ trợ.";
      case 422:
        return "Thông tin tài liệu chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 429: {
        const retryAfterSeconds =
          error instanceof ApiClientError ? error.retryAfterSeconds : null;
        return retryAfterSeconds !== null && retryAfterSeconds > 0
          ? `Hệ thống đang giới hạn số lượng yêu cầu. Vui lòng đợi ${retryAfterSeconds} giây rồi thử lại.`
          : "Hệ thống đang giới hạn số lượng yêu cầu. Vui lòng đợi rồi thử lại.";
      }
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

function isDirectStorageError(
  error: unknown,
): error is { readonly name: "DirectStorageError"; readonly status: number; readonly code?: string } {
  return (
    error instanceof Error &&
    error.name === "DirectStorageError" &&
    typeof (error as { status?: unknown }).status === "number"
  );
}

export class CreditOpsApiClient implements CreditOpsApi {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async listCases(): Promise<CreditCaseListDto> {
    return parseCreditCaseList(await this.request("/api/v1/cases"));
  }

  async getCase(caseId: string): Promise<CreditCaseDto> {
    return parseCreditCase(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}`),
    );
  }

  async createCase(request: CreateCaseRequestDto): Promise<CreditCaseDto> {
    return parseCreditCase(
      await this.request("/api/v1/cases", {
        method: "POST",
        body: JSON.stringify(request),
      }),
    );
  }

  async createUploadIntent(
    caseId: string,
    request: CreateUploadIntentRequestDto,
  ): Promise<UploadIntentDto> {
    return parseUploadIntent(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/upload-intents`,
        { method: "POST", body: JSON.stringify(request) },
      ),
    );
  }

  async completeUploadIntent(
    intentId: string,
    idempotencyKey: string,
  ): Promise<CompleteUploadResponseDto> {
    return parseCompleteUpload(
      await this.request(
        `/api/v1/upload-intents/${encodeURIComponent(intentId)}/complete`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: "{}",
        },
      ),
    );
  }

  async getDocumentReview(documentId: string): Promise<DocumentReviewDto> {
    return parseDocumentReview(
      await this.request(
        `/api/v1/documents/${encodeURIComponent(documentId)}/review`,
      ),
    );
  }

  async confirmDocument(
    documentId: string,
    request: ConfirmDocumentRequestDto,
  ): Promise<void> {
    // Success is any 2xx; the confirmation response body is not consumed here.
    await this.request(
      `/api/v1/documents/${encodeURIComponent(documentId)}/confirmations`,
      { method: "POST", body: JSON.stringify(request) },
    );
  }

  async listEvidence(caseId: string): Promise<EvidenceListDto> {
    return parseEvidenceList(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/evidence`,
      ),
    );
  }

  async listConflicts(caseId: string): Promise<ConflictListDto> {
    return parseConflictList(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/conflicts`,
      ),
    );
  }

  // Completes assigned intake with an EMPTY body. On success returns the
  // handoff (201 created, or 200 idempotent repeat with created=false). A 409
  // INTAKE_INCOMPLETE throws with details.reasons — never treated as success.
  async completeIntake(caseId: string): Promise<IntakeCompletionResultDto> {
    return parseIntakeCompletion(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/intake-completion`,
        { method: "POST", body: "{}" },
      ),
    );
  }

  async getHandoff(caseId: string): Promise<HandoffDto> {
    return parseHandoff(
      await this.request(`/api/v1/cases/${encodeURIComponent(caseId)}/handoffs`),
    );
  }

  async listAuditEvents(
    caseId: string,
    cursor?: string | null,
    limit?: number,
  ): Promise<AuditEventListDto> {
    const query = new URLSearchParams();
    if (cursor) query.set("cursor", cursor);
    if (limit !== undefined) query.set("limit", String(limit));
    const suffix = query.toString();
    return parseAuditEventList(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/audit-events${suffix ? `?${suffix}` : ""}`,
      ),
    );
  }

  // Callers that need the raw HTTP status alongside the parsed body (for
  // example to detect a 202 Accepted queued/running response) can use this
  // instead of the narrower typed methods above, which only ever surface the
  // parsed body on success.
  async requestWithStatus(
    path: string,
    init: RequestInit = {},
  ): Promise<{ status: number; body: unknown }> {
    return this.performRequest(path, init);
  }

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    return (await this.performRequest(path, init)).body;
  }

  private async performRequest(
    path: string,
    init: RequestInit = {},
  ): Promise<{ status: number; body: unknown }> {
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
      // Never auto-retry here: 429 handling is limited to surfacing the
      // server's Retry-After hint so a caller can respect it.
      throw new ApiClientError(
        response.status,
        apiError?.code ?? "REQUEST_FAILED",
        apiError?.messageVi || "Yêu cầu không thành công.",
        apiError?.retryable ?? response.status >= 500,
        response.status === 429 ? parseRetryAfterSeconds(response) : null,
        parseErrorDetails(body),
      );
    }
    return { status: response.status, body };
  }
}

function isMutation(method: string | undefined): boolean {
  return method !== undefined && !["GET", "HEAD"].includes(method.toUpperCase());
}

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

// Only the integer-seconds form of Retry-After is recognized; an absent
// header or the HTTP-date form both fail closed to null so callers never
// compute a wait time from a value they can't trust.
function parseRetryAfterSeconds(response: Response): number | null {
  const header = response.headers.get("Retry-After");
  if (header === null) return null;
  const trimmed = header.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  return Number.parseInt(trimmed, 10);
}

function parseErrorDetails(body: unknown): Record<string, unknown> | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  const details = (body as Record<string, unknown>).details;
  if (typeof details !== "object" || details === null || Array.isArray(details)) {
    return null;
  }
  return details as Record<string, unknown>;
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

export const creditOpsApi = new CreditOpsApiClient();
