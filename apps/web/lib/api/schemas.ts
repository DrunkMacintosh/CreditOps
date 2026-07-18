import type {
  ApiErrorDto,
  CandidateFactDto,
  CaseCapabilities,
  CompleteUploadResponseDto,
  ConfirmedFactDto,
  ConflictDto,
  ConflictListDto,
  ConflictSourceDto,
  CreditCaseListDto,
  CreditCaseDto,
  DocumentReviewDto,
  DocumentStage,
  EvidenceListDto,
  PageRegionDto,
  TaskStatus,
  TaskStatusDto,
  UploadIntentDto,
} from "./contracts";

const TASK_STATUSES = new Set<TaskStatus>([
  "PENDING",
  "RUNNING",
  "RETRY_WAIT",
  "SUCCEEDED",
  "FAILED_MANUAL_REVIEW",
  "SUPERSEDED",
]);

const DOCUMENT_STAGES = new Set<DocumentStage>([
  "REGISTERED",
  "SECURITY_VALIDATED",
  "PARSED",
  "CLASSIFIED",
  "EXTRACTED",
  "INDEXED",
  "READY_FOR_OFFICER_REVIEW",
]);

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`Phản hồi ${label} không đúng định dạng.`);
  }
  return value as Record<string, unknown>;
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Phản hồi thiếu ${label}.`);
  }
  return value;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function positiveInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
    throw new Error(`Phản hồi thiếu ${label} hợp lệ.`);
  }
  return value;
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`Phản hồi thiếu ${label} hợp lệ.`);
  }
  return value;
}

function booleanWithDefault(value: unknown, fallback: boolean, label: string): boolean {
  if (value === undefined || value === null) return fallback;
  return boolean(value, label);
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Phản hồi thiếu ${label} hợp lệ.`);
  }
  return value;
}

function unitInterval(value: unknown, label: string): number {
  const number = finiteNumber(value, label);
  if (number < 0 || number > 1) {
    throw new Error(`Phản hồi ${label} nằm ngoài khoảng cho phép.`);
  }
  return number;
}

function openUnitInterval(value: unknown, label: string): number {
  const number = finiteNumber(value, label);
  if (number <= 0 || number > 1) {
    throw new Error(`Phản hồi ${label} nằm ngoài khoảng cho phép.`);
  }
  return number;
}

function nullablePositiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 1
    ? value
    : null;
}

function factValue(value: unknown, label: string): string | number | boolean {
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  throw new Error(`Phản hồi ${label} không đúng định dạng.`);
}

function parseHeaders(value: unknown): Readonly<Record<string, string>> {
  if (value === undefined || value === null) return {};
  const raw = record(value, "headers");
  const headers: Record<string, string> = {};
  for (const [name, headerValue] of Object.entries(raw)) {
    if (typeof headerValue !== "string") {
      throw new Error("Phản hồi headers không đúng định dạng.");
    }
    headers[name] = headerValue;
  }
  return headers;
}

function parseCapabilities(value: unknown): CaseCapabilities {
  const raw = record(value, "capabilities");
  return {
    canUpload: boolean(raw.canUpload, "canUpload"),
    canConfirm: boolean(raw.canConfirm, "canConfirm"),
    canCompleteIntake: boolean(raw.canCompleteIntake, "canCompleteIntake"),
  };
}

export function parseCreditCase(value: unknown): CreditCaseDto {
  const raw = record(value, "hồ sơ");
  const financingRequest =
    typeof raw.financingRequest === "object" && raw.financingRequest !== null
      ? record(raw.financingRequest, "yêu cầu cấp vốn")
      : {};

  return {
    id: string(raw.id, "id hồ sơ"),
    version: positiveInteger(raw.version ?? raw.caseVersion, "phiên bản hồ sơ"),
    assignedOfficerId: string(raw.assignedOfficerId, "cán bộ phụ trách"),
    requestedAmount: nullableString(
      raw.requestedAmount ?? financingRequest.requestedAmount,
    ),
    purpose: nullableString(raw.purpose ?? financingRequest.purpose),
    workflowState: nullableString(raw.workflowState),
    updatedAt: nullableString(raw.updatedAt),
    capabilities: parseCapabilities(raw.capabilities),
  };
}

export function parseCreditCaseList(value: unknown): CreditCaseListDto {
  const raw = record(value, "danh sách hồ sơ");
  const items = raw.items;
  if (!Array.isArray(items)) {
    throw new Error("Phản hồi danh sách hồ sơ không đúng định dạng.");
  }
  const capabilities = raw.capabilities === undefined
    ? {}
    : record(raw.capabilities, "quyền danh sách hồ sơ");
  const nextCursor = raw.nextCursor;
  if (nextCursor !== null && typeof nextCursor !== "string") {
    throw new Error("Phản hồi nextCursor không đúng định dạng.");
  }
  return {
    items: items.map(parseCreditCase),
    nextCursor,
    capabilities: {
      canCreateCase: boolean(capabilities.canCreateCase, "canCreateCase"),
    },
  };
}

export function parseUploadIntent(value: unknown): UploadIntentDto {
  const raw = record(value, "phiên tải lên");
  const mode = string(raw.mode, "chế độ tải lên");
  const headers = parseHeaders(raw.headers);
  if (headerValue(headers, "x-upsert")?.trim().toLowerCase() === "true") {
    throw new Error("Ủy quyền tải lên không được bật upsert.");
  }
  const common = {
    intentId: string(raw.intentId, "id phiên tải lên"),
    expiresAt: string(raw.expiresAt, "thời điểm hết hạn"),
    uploadUrl: string(raw.uploadUrl, "địa chỉ tải lên"),
    headers,
  };

  if (mode === "SIGNED") {
    const method = string(raw.method, "phương thức tải lên");
    if (method !== "POST" && method !== "PUT") {
      throw new Error("Phương thức tải lên không được hỗ trợ.");
    }
    return { ...common, mode: "SIGNED", method };
  }
  if (mode === "RESUMABLE") {
    requireObjectBindingMetadata(headers);
    return { ...common, mode: "RESUMABLE" };
  }
  throw new Error("Chế độ tải lên không được hỗ trợ.");
}

export function parseTaskStatus(value: unknown): TaskStatusDto {
  const raw = record(value, "trạng thái tác vụ");
  const status = string(raw.status, "trạng thái") as TaskStatus;
  if (!TASK_STATUSES.has(status)) {
    throw new Error("Trạng thái tác vụ không được hỗ trợ.");
  }
  return { id: string(raw.id, "id tác vụ"), status };
}

export function parseCompleteUpload(value: unknown): CompleteUploadResponseDto {
  const raw = record(value, "hoàn tất tải lên");
  const outcome = string(raw.outcome, "kết quả hoàn tất");
  if (outcome === "DUPLICATE") {
    if (
      raw.documentId !== undefined ||
      raw.documentVersionId !== undefined ||
      raw.task !== undefined
    ) {
      throw new Error("Kết quả tài liệu trùng lặp chứa trạng thái mâu thuẫn.");
    }
    return {
      outcome,
      duplicateOfDocumentId: string(
        raw.duplicateOfDocumentId,
        "tài liệu trùng lặp",
      ),
    };
  }
  if (outcome === "REGISTERED") {
    if (raw.duplicateOfDocumentId !== undefined) {
      throw new Error("Kết quả đăng ký tài liệu chứa trạng thái mâu thuẫn.");
    }
    return {
      outcome,
      documentId: string(raw.documentId, "id tài liệu"),
      documentVersionId: string(raw.documentVersionId, "id phiên bản tài liệu"),
      task: parseTaskStatus(raw.task),
    };
  }
  throw new Error("Kết quả hoàn tất tải lên không được hỗ trợ.");
}

export function parsePageRegion(value: unknown): PageRegionDto {
  const raw = record(value, "vùng tài liệu");
  const page = positiveInteger(raw.page, "trang tài liệu");
  const x = unitInterval(raw.x, "tọa độ x");
  const y = unitInterval(raw.y, "tọa độ y");
  const width = openUnitInterval(raw.width, "chiều rộng vùng");
  const height = openUnitInterval(raw.height, "chiều cao vùng");
  if (x + width > 1 || y + height > 1) {
    throw new Error("Vùng tài liệu vượt quá kích thước trang chuẩn hóa.");
  }
  return { page, x, y, width, height };
}

export function parseCandidateFact(value: unknown): CandidateFactDto {
  const raw = record(value, "chứng cứ đề xuất");
  return {
    id: string(raw.id, "id chứng cứ"),
    caseId: string(raw.caseId, "id hồ sơ"),
    caseVersion: positiveInteger(raw.caseVersion, "phiên bản hồ sơ"),
    documentVersionId: string(raw.documentVersionId, "id phiên bản tài liệu"),
    fieldKey: string(raw.fieldKey, "khóa trường thông tin"),
    proposedValue: factValue(raw.proposedValue, "giá trị đề xuất"),
    confidence: unitInterval(raw.confidence, "độ tin cậy"),
    source: parsePageRegion(raw.source),
  };
}

export function parseDocumentReview(value: unknown): DocumentReviewDto {
  const raw = record(value, "phiên rà soát tài liệu");
  const stage = string(raw.stage, "giai đoạn tài liệu") as DocumentStage;
  if (!DOCUMENT_STAGES.has(stage)) {
    throw new Error("Giai đoạn tài liệu không được hỗ trợ.");
  }
  const candidates = raw.candidates;
  if (!Array.isArray(candidates)) {
    throw new Error("Phản hồi danh sách chứng cứ không đúng định dạng.");
  }
  return {
    documentId: string(raw.documentId, "id tài liệu"),
    caseId: string(raw.caseId, "id hồ sơ"),
    documentVersionId: string(raw.documentVersionId, "id phiên bản tài liệu"),
    documentVersion: positiveInteger(
      raw.documentVersion ?? raw.expectedDocumentVersion,
      "phiên bản tài liệu",
    ),
    stage,
    fileName: nullableString(raw.fileName),
    pageCount: nullablePositiveInteger(raw.pageCount),
    candidates: candidates.map(parseCandidateFact),
  };
}

export function parseConfirmedFact(value: unknown): ConfirmedFactDto {
  const raw = record(value, "chứng cứ đã xác nhận");
  return {
    id: string(raw.id, "id chứng cứ đã xác nhận"),
    caseId: string(raw.caseId, "id hồ sơ"),
    caseVersion: positiveInteger(raw.caseVersion, "phiên bản hồ sơ"),
    candidateId: string(raw.candidateId, "id chứng cứ đề xuất"),
    confirmationId: string(raw.confirmationId, "id xác nhận"),
    documentVersionId: string(raw.documentVersionId, "id phiên bản tài liệu"),
    fieldKey: string(raw.fieldKey, "khóa trường thông tin"),
    value: factValue(raw.value, "giá trị đã xác nhận"),
    candidateValue: factValue(raw.candidateValue, "giá trị đề xuất"),
    source: parsePageRegion(raw.source),
    confirmedAt: string(raw.confirmedAt, "thời điểm xác nhận"),
    stale: booleanWithDefault(raw.stale, false, "cờ lỗi thời"),
  };
}

export function parseEvidenceList(value: unknown): EvidenceListDto {
  const raw = record(value, "danh sách chứng cứ");
  const items = raw.items ?? raw.facts;
  if (!Array.isArray(items)) {
    throw new Error("Phản hồi danh sách chứng cứ không đúng định dạng.");
  }
  return { items: items.map(parseConfirmedFact) };
}

function parseConflictSource(value: unknown): ConflictSourceDto {
  const raw = record(value, "nguồn xung đột");
  return {
    documentVersionId: string(raw.documentVersionId, "id phiên bản tài liệu"),
    value: factValue(raw.value, "giá trị nguồn"),
    source:
      raw.source === undefined || raw.source === null
        ? null
        : parsePageRegion(raw.source),
  };
}

export function parseConflict(value: unknown): ConflictDto {
  const raw = record(value, "xung đột chứng cứ");
  const sources = raw.sources;
  if (!Array.isArray(sources) || sources.length < 2) {
    throw new Error("Xung đột chứng cứ phải giữ lại từ hai nguồn trở lên.");
  }
  return {
    id: string(raw.id, "id xung đột"),
    caseId: string(raw.caseId, "id hồ sơ"),
    caseVersion: positiveInteger(raw.caseVersion, "phiên bản hồ sơ"),
    fieldKey: string(raw.fieldKey, "khóa trường thông tin"),
    sources: sources.map(parseConflictSource),
    detectedAt: nullableString(raw.detectedAt),
    stale: booleanWithDefault(raw.stale, false, "cờ lỗi thời"),
  };
}

export function parseConflictList(value: unknown): ConflictListDto {
  const raw = record(value, "danh sách xung đột");
  const items = raw.items;
  if (!Array.isArray(items)) {
    throw new Error("Phản hồi danh sách xung đột không đúng định dạng.");
  }
  return { items: items.map(parseConflict) };
}

function headerValue(
  headers: Readonly<Record<string, string>>,
  name: string,
): string | undefined {
  const normalizedName = name.toLowerCase();
  return Object.entries(headers).find(
    ([header]) => header.toLowerCase() === normalizedName,
  )?.[1];
}

function requireObjectBindingMetadata(
  headers: Readonly<Record<string, string>>,
): void {
  const metadata = headerValue(headers, "Upload-Metadata");
  const entries = new Map(
    (metadata ?? "").split(",").map((entry) => {
      const [name, encodedValue, ...extra] = entry.trim().split(/\s+/);
      return extra.length === 0 ? [name, encodedValue] : ["", ""];
    }),
  );
  if (!validBase64(entries.get("bucketName")) || !validBase64(entries.get("objectName"))) {
    throw new Error(
      "Upload-Metadata phải ràng buộc bucketName và objectName hợp lệ.",
    );
  }
}

function validBase64(value: string | undefined): boolean {
  if (!value || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) return false;
  try {
    return atob(value).length > 0;
  } catch {
    return false;
  }
}

export function parseApiError(value: unknown): ApiErrorDto | null {
  try {
    const raw = record(value, "lỗi");
    return {
      code: string(raw.code, "mã lỗi"),
      messageVi: typeof raw.messageVi === "string" ? raw.messageVi : "",
      correlationId: nullableString(raw.correlationId),
      retryable: raw.retryable === true,
    };
  } catch {
    return null;
  }
}
