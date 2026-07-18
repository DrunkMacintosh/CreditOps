// Self-contained API bindings for the stage-8 Contract Package screen
// ("Hồ sơ hợp đồng"). Mirrors lib/api/client.ts conventions — BFF base
// "/api/creditops", the "__Host-creditops-csrf" cookie surfaced as the
// "x-creditops-csrf" header on mutations, ApiClientError-style failures — but
// shares no mutable state with it.
//
// Backend truth mirrored here: services/api/src/creditops/api/contract_packages.py.
//   GET  /api/v1/cases/{caseId}/contract-packages                      -> PackageView (404 NO_CONTRACT_PACKAGE = empty)
//   POST /api/v1/cases/{caseId}/contract-packages                      -> 201/200 ContractPackage
//   POST /api/v1/cases/{caseId}/contract-packages/redlines             -> 201 AddRedlineResult
//   POST /api/v1/cases/{caseId}/contract-packages/approve              -> GateWrite (HG_CONTRACT_PACKAGE_APPROVED)
//   POST /api/v1/cases/{caseId}/contract-packages/signature-authority  -> GateWrite (HG_SIGNATURE_AUTHORITY_CONFIRMED)
//   POST /api/v1/cases/{caseId}/contract-packages/sign                 -> SignResult (HG_CONTRACTS_SIGNED, MOCK evidence)
//
// The contract text is a deterministic template render of the approved terms;
// nothing here executes real signing. Approval re-runs material-change
// detection server-side: a mismatch fences the package (409
// MATERIAL_CHANGE_DETECTED). Signing records MOCK signature evidence only.

import { ApiClientError } from "./client";

const BFF_BASE_URL = "/api/creditops";
const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
const CSRF_HEADER_NAME = "x-creditops-csrf";

// --- Domain enums (mirror services/.../domain/contract_packages.py) ---

export type GateStatus = "OPEN" | "SATISFIED";

export type ContractPackageState =
  | "DRAFT"
  | "REDLINED"
  | "MATERIAL_CHANGE_DETECTED"
  | "READY_FOR_SIGNATURE";

export type SignatureEvidenceKind = "MOCK_SIGNATURE";

// --- Response shapes ---

export interface ContractPackage {
  id: string;
  caseId: string;
  caseVersion: number;
  decisionId: string;
  termSnapshotHash: string;
  content: string;
  contentHash: string;
  packageVersion: number;
  state: ContractPackageState | string;
  createdBy: string;
  createdAt: string;
}

export interface ContractRedline {
  id: string;
  packageId: string;
  redlineVersion: number;
  changeNote: string;
  changedContent: string;
  changedContentHash: string;
  createdBy: string;
  createdAt: string;
}

export interface SignatureEvidence {
  id: string;
  packageId: string;
  kind: SignatureEvidenceKind | string;
  signerNames: string[];
  evidenceNote: string | null;
  recordedBy: string;
  createdAt: string;
}

export interface ContractPackageView {
  package: ContractPackage;
  redlines: ContractRedline[];
  signatureEvidence: SignatureEvidence | null;
}

export interface AddRedlineResult {
  redline: ContractRedline;
  package: ContractPackage;
}

export interface ContractGateWrite {
  gateType: string;
  status: GateStatus | string;
  packageId: string;
  dispositionRef: string;
}

export interface SignResult {
  gateType: string;
  status: GateStatus | string;
  package: ContractPackage;
  signatureEvidence: SignatureEvidence;
  dispositionRef: string;
}

export interface AddRedlineInput {
  changeNote: string;
  changedContent: string;
}

export interface RationaleInput {
  rationale: string;
}

export interface SignInput {
  signerNames: string[];
  evidenceNote?: string;
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

function strArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(str) : [];
}

function parsePackage(value: unknown): ContractPackage {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    caseId: str(raw.caseId),
    caseVersion: num(raw.caseVersion),
    decisionId: str(raw.decisionId),
    termSnapshotHash: str(raw.termSnapshotHash),
    content: str(raw.content),
    contentHash: str(raw.contentHash),
    packageVersion: num(raw.packageVersion),
    state: str(raw.state),
    createdBy: str(raw.createdBy),
    createdAt: str(raw.createdAt),
  };
}

function parseRedline(value: unknown): ContractRedline {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    packageId: str(raw.packageId),
    redlineVersion: num(raw.redlineVersion),
    changeNote: str(raw.changeNote),
    changedContent: str(raw.changedContent),
    changedContentHash: str(raw.changedContentHash),
    createdBy: str(raw.createdBy),
    createdAt: str(raw.createdAt),
  };
}

function parseEvidence(value: unknown): SignatureEvidence {
  const raw = asRecord(value);
  return {
    id: str(raw.id),
    packageId: str(raw.packageId),
    kind: str(raw.kind),
    signerNames: strArray(raw.signerNames),
    evidenceNote: strOrNull(raw.evidenceNote),
    recordedBy: str(raw.recordedBy),
    createdAt: str(raw.createdAt),
  };
}

export function parseContractPackageView(value: unknown): ContractPackageView {
  const raw = asRecord(value);
  return {
    package: parsePackage(raw.package),
    redlines: Array.isArray(raw.redlines) ? raw.redlines.map(parseRedline) : [],
    signatureEvidence:
      raw.signatureEvidence == null ? null : parseEvidence(raw.signatureEvidence),
  };
}

export function parseContractPackage(value: unknown): ContractPackage {
  return parsePackage(value);
}

export function parseAddRedlineResult(value: unknown): AddRedlineResult {
  const raw = asRecord(value);
  return { redline: parseRedline(raw.redline), package: parsePackage(raw.package) };
}

export function parseContractGateWrite(value: unknown): ContractGateWrite {
  const raw = asRecord(value);
  return {
    gateType: str(raw.gateType),
    status: str(raw.status),
    packageId: str(raw.packageId),
    dispositionRef: str(raw.dispositionRef),
  };
}

export function parseSignResult(value: unknown): SignResult {
  const raw = asRecord(value);
  return {
    gateType: str(raw.gateType),
    status: str(raw.status),
    package: parsePackage(raw.package),
    signatureEvidence: parseEvidence(raw.signatureEvidence),
    dispositionRef: str(raw.dispositionRef),
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

export class ContractPackagesApiClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl = BFF_BASE_URL,
    private readonly fetcher: Fetcher = (input, init) => fetch(input, init),
    private readonly csrfTokenProvider: CsrfTokenProvider = readBrowserCsrfToken,
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // Reads the current package, its versioned redlines, and signature evidence.
  // 404 NO_CONTRACT_PACKAGE means none has been drafted yet — an empty state.
  async getView(caseId: string): Promise<ContractPackageView> {
    return parseContractPackageView(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/contract-packages`,
      ),
    );
  }

  // Deterministically renders + persists the first draft from the permitting
  // decision's approved terms. 409 NO_PERMITTING_DECISION otherwise.
  async createPackage(caseId: string): Promise<ContractPackage> {
    return parseContractPackage(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/contract-packages`,
        { method: "POST", body: "{}" },
      ),
    );
  }

  // Appends a versioned redline (a new REDLINED package version), never an edit.
  async addRedline(caseId: string, input: AddRedlineInput): Promise<AddRedlineResult> {
    return parseAddRedlineResult(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/contract-packages/redlines`,
        { method: "POST", body: JSON.stringify(input) },
      ),
    );
  }

  // Approves the current package. 409 MATERIAL_CHANGE_DETECTED fences it when
  // the terms no longer match the current decision snapshot.
  async approve(caseId: string, input: RationaleInput): Promise<ContractGateWrite> {
    return parseContractGateWrite(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/contract-packages/approve`,
        { method: "POST", body: JSON.stringify(input) },
      ),
    );
  }

  // Confirms signing authority. 409 GATE_ORDER_VIOLATION if the package is not
  // yet approved.
  async confirmSignatureAuthority(
    caseId: string,
    input: RationaleInput,
  ): Promise<ContractGateWrite> {
    return parseContractGateWrite(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/contract-packages/signature-authority`,
        { method: "POST", body: JSON.stringify(input) },
      ),
    );
  }

  // Records MOCK signature evidence. Requires BOTH prior gates satisfied (409
  // GATE_ORDER_VIOLATION otherwise); 409 CONTRACT_ALREADY_SIGNED on a repeat.
  async sign(caseId: string, input: SignInput): Promise<SignResult> {
    const body: Record<string, unknown> = { signerNames: input.signerNames };
    if (input.evidenceNote) body.evidenceNote = input.evidenceNote;
    return parseSignResult(
      await this.request(
        `/api/v1/cases/${encodeURIComponent(caseId)}/contract-packages/sign`,
        { method: "POST", body: JSON.stringify(body) },
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

export const contractPackagesApi = new ContractPackagesApiClient();

// GET returns 404 with this code when no package has been drafted for the case
// version yet — an empty state (draft on explicit action), not an error.
export function isContractPackageNotAvailable(error: unknown): boolean {
  return error instanceof ApiClientError && error.code === "NO_CONTRACT_PACKAGE";
}

// A write failed because the package terms no longer match the current credit
// decision: the package is fenced and the case must return to the decision
// stage. Rendered as a distinct blocking state, never a routine 409.
export function isMaterialChange(error: unknown): boolean {
  return error instanceof ApiClientError && error.code === "MATERIAL_CHANGE_DETECTED";
}

const GENERIC_MESSAGES = new Set(["Không thể hoàn tất yêu cầu.", "Yêu cầu không thành công."]);

export function getContractError(error: unknown): string {
  if (error instanceof ApiClientError) {
    if (error.message && !GENERIC_MESSAGES.has(error.message)) {
      return error.message;
    }
    switch (error.status) {
      case 401:
        return "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.";
      case 403:
        return "Bạn không có vai trò để thao tác trên hồ sơ hợp đồng.";
      case 404:
        return "Không tìm thấy hồ sơ hoặc gói hợp đồng. Vui lòng tải lại.";
      case 409:
        return "Trạng thái hồ sơ hợp đồng đã thay đổi. Vui lòng tải lại để xem bản mới nhất.";
      case 422:
        return "Thông tin chưa hợp lệ. Vui lòng kiểm tra và thử lại.";
      case 503:
        return "Dịch vụ hồ sơ hợp đồng chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    }
  }
  return "Không thể hoàn tất yêu cầu. Vui lòng thử lại.";
}

// --- Display labels ---

export const GATE_STATUS_LABELS: Record<GateStatus, string> = {
  OPEN: "Đang chờ",
  SATISFIED: "Đạt",
};

export const PACKAGE_STATE_LABELS: Record<ContractPackageState, string> = {
  DRAFT: "Bản nháp",
  REDLINED: "Đã chỉnh sửa pháp lý",
  MATERIAL_CHANGE_DETECTED: "Phát hiện thay đổi trọng yếu",
  READY_FOR_SIGNATURE: "Sẵn sàng ký",
};

export const SIGNATURE_KIND_LABELS: Record<SignatureEvidenceKind, string> = {
  MOCK_SIGNATURE: "Chữ ký mô phỏng",
};

// The mock-contract disclaimer the contract screen must always display.
export const MOCK_CONTRACT_LABEL_VI = "Hợp đồng mô phỏng — không có hiệu lực pháp lý.";

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
