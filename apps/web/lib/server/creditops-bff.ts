// The future identity callback must issue this with HttpOnly, Secure,
// SameSite=Strict, Path=/, and no Domain. This BFF never issues the token cookie.
import { getCloudRunServerlessAuthorization } from "./cloud-run-auth";

export const SESSION_COOKIE_NAME = "__Host-creditops-workforce";
export const CSRF_COOKIE_NAME = "__Host-creditops-csrf";
export const CSRF_HEADER_NAME = "x-creditops-csrf";

const MAX_REQUEST_BYTES = 1024 * 1024;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_DECLARED_FILE_BYTES = 100 * 1024 * 1024;
const SAFE_RESPONSE_HEADERS = [
  "content-type",
  "retry-after",
  "x-correlation-id",
  "x-request-id",
];
const SAFE_ID = /^[A-Za-z0-9_-]+$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FACT_DISPOSITIONS = new Set([
  "ACCEPTED",
  "CORRECTED",
  "ABSENT",
  "UNREADABLE",
]);
const MAX_DISPOSITIONS = 200;
const MAX_DOCUMENT_VERSION = 1_000_000;
const ACCEPTED_UPLOAD_TYPES = new Map([
  [".pdf", "application/pdf"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [
    ".docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ],
  [
    ".xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ],
]);

interface ProxyDependencies {
  fetcher?: typeof fetch;
  upstreamBaseUrl?: string;
  serverlessAuthorization?: (request: Request) => Promise<string>;
}

export async function proxyCreditOpsRequest(
  request: Request,
  pathSegments: string[],
  dependencies: ProxyDependencies = {},
): Promise<Response> {
  const method = request.method.toUpperCase();
  if (!allowlisted(method, pathSegments)) {
    return jsonError(404, "BFF_ROUTE_NOT_ALLOWED");
  }

  const incomingUrl = new URL(request.url);
  const canonicalSearch = validateAndReconstructSearch(
    method,
    pathSegments,
    incomingUrl.searchParams,
  );
  if (canonicalSearch === null) {
    return jsonError(400, "QUERY_INVALID");
  }

  const token = readCookie(request.headers.get("cookie"), SESSION_COOKIE_NAME);
  if (!validCredential(token)) {
    return jsonError(401, "SESSION_REQUIRED");
  }

  const hasBody = method !== "GET" && method !== "HEAD";
  if (hasBody) {
    const mediaType = (request.headers.get("content-type") ?? "")
      .split(";", 1)[0]
      .trim()
      .toLowerCase();
    if (mediaType !== "application/json") {
      return jsonError(415, "JSON_BODY_REQUIRED");
    }
    if (!validCsrf(request)) {
      return jsonError(403, "CSRF_VALIDATION_FAILED");
    }
  }

  const idempotencyKey = request.headers.get("idempotency-key");
  if (
    isUploadCompletion(method, pathSegments) &&
    (!validOpaqueHeader(idempotencyKey) || idempotencyKey.length > 256)
  ) {
    return jsonError(400, "IDEMPOTENCY_KEY_REQUIRED");
  }

  const configuredBase = dependencies.upstreamBaseUrl ?? process.env.CREDITOPS_API_URL;
  const upstreamBase = parseUpstreamBase(configuredBase);
  if (!upstreamBase) {
    return jsonError(503, "UPSTREAM_NOT_CONFIGURED");
  }

  let serverlessToken: string;
  try {
    serverlessToken = await (dependencies.serverlessAuthorization ?? ((incoming) =>
      getCloudRunServerlessAuthorization(incoming, { audience: upstreamBase.origin })))(request);
  } catch {
    return jsonError(503, "CLOUD_RUN_AUTH_NOT_CONFIGURED");
  }

  let body: string | undefined;
  if (hasBody) {
    if (declaredBodyTooLarge(request.headers, MAX_REQUEST_BYTES)) {
      await cancelBody(request.body);
      return jsonError(413, "JSON_BODY_TOO_LARGE");
    }
    let requestBytes: Uint8Array<ArrayBuffer> | null;
    try {
      requestBytes = await readBoundedBody(request.body, MAX_REQUEST_BYTES);
    } catch {
      return jsonError(400, "JSON_BODY_UNREADABLE");
    }
    if (requestBytes === null) {
      return jsonError(413, "JSON_BODY_TOO_LARGE");
    }
    const canonicalBody = validateAndReconstructMutation(
      pathSegments,
      decodeJson(requestBytes),
    );
    if (canonicalBody === null) {
      return jsonError(422, "JSON_BODY_INVALID");
    }
    body = JSON.stringify(canonicalBody);
  }

  const upstreamHeaders = new Headers({
    accept: "application/json",
    authorization: `Bearer ${token}`,
    "x-serverless-authorization": `Bearer ${serverlessToken}`,
  });
  if (hasBody) upstreamHeaders.set("content-type", "application/json");
  if (idempotencyKey && validOpaqueHeader(idempotencyKey)) {
    upstreamHeaders.set("idempotency-key", idempotencyKey);
  }

  const upstreamUrl = new URL(`/${pathSegments.join("/")}`, upstreamBase);
  upstreamUrl.search = canonicalSearch;

  let upstreamResponse: Response;
  try {
    upstreamResponse = await (dependencies.fetcher ?? fetch)(upstreamUrl.toString(), {
      method,
      headers: upstreamHeaders,
      body,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return jsonError(502, "UPSTREAM_UNAVAILABLE");
  }

  const responseType = upstreamResponse.headers.get("content-type") ?? "";
  if (!isJsonMediaType(responseType)) {
    await cancelBody(upstreamResponse.body);
    return jsonError(502, "UPSTREAM_NON_JSON_RESPONSE");
  }
  if (declaredBodyTooLarge(upstreamResponse.headers, MAX_RESPONSE_BYTES)) {
    await cancelBody(upstreamResponse.body);
    return jsonError(502, "UPSTREAM_RESPONSE_TOO_LARGE");
  }
  let responseBody: Uint8Array<ArrayBuffer> | null;
  try {
    responseBody = await readBoundedBody(upstreamResponse.body, MAX_RESPONSE_BYTES);
  } catch {
    return jsonError(502, "UPSTREAM_RESPONSE_UNREADABLE");
  }
  if (responseBody === null) {
    return jsonError(502, "UPSTREAM_RESPONSE_TOO_LARGE");
  }

  const responseHeaders = new Headers({ "cache-control": "no-store" });
  for (const name of SAFE_RESPONSE_HEADERS) {
    const value = upstreamResponse.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(responseBody, {
    status: upstreamResponse.status,
    headers: responseHeaders,
  });
}

function validateAndReconstructSearch(
  method: string,
  segments: string[],
  parameters: URLSearchParams,
): string | null {
  if (isCursorPaginatedListRoute(method, segments)) {
    return reconstructCursorLimitQuery(parameters);
  }
  if (isLimitOnlyListRoute(method, segments)) {
    return reconstructLimitOnlyQuery(parameters);
  }
  if (isLedgerAsOfRoute(method, segments)) {
    return reconstructAsOfQuery(parameters);
  }
  return [...parameters.entries()].length === 0 ? "" : null;
}

// The repayment-ledger read (``GET .../repayments/{id}/ledger``) is the only
// stage 11-14 route that accepts a query: a single optional ``asOf`` calendar
// date. Same reconstruct-from-scratch discipline as the list routes — any other
// parameter, a repeated ``asOf``, or a non-date fails closed.
function isLedgerAsOfRoute(method: string, segments: string[]): boolean {
  if (method !== "GET") return false;
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/repayments\/[A-Za-z0-9_-]+\/ledger$/.test(
    `/${segments.join("/")}`,
  );
}

function reconstructAsOfQuery(parameters: URLSearchParams): string | null {
  const entries = [...parameters.entries()];
  if (entries.some(([name]) => name !== "asOf")) return null;
  if (parameters.getAll("asOf").length > 1) return null;
  const asOf = parameters.get("asOf");
  if (asOf === null) return "";
  const date = normalizedDate(asOf);
  if (date === null) return null;
  const canonical = new URLSearchParams();
  canonical.set("asOf", date);
  return `?${canonical.toString()}`;
}

function reconstructCursorLimitQuery(parameters: URLSearchParams): string | null {
  const entries = [...parameters.entries()];
  if (entries.some(([name]) => name !== "cursor" && name !== "limit")) {
    return null;
  }
  if (parameters.getAll("cursor").length > 1 || parameters.getAll("limit").length > 1) {
    return null;
  }
  const cursor = parameters.get("cursor");
  const limit = parameters.get("limit");
  if ((cursor !== null && !UUID.test(cursor)) || (limit !== null && !validLimit(limit))) {
    return null;
  }

  const canonical = new URLSearchParams();
  if (cursor !== null) canonical.set("cursor", cursor.toLowerCase());
  if (limit !== null) canonical.set("limit", String(Number(limit)));
  const query = canonical.toString();
  return query ? `?${query}` : "";
}

// The work-queue list route accepts only a bounded ``limit`` and no cursor.
// Same reconstruct-from-scratch discipline as the cursor routes above: any
// other parameter, a repeated ``limit``, or an out-of-range value fails closed.
function reconstructLimitOnlyQuery(parameters: URLSearchParams): string | null {
  const entries = [...parameters.entries()];
  if (entries.some(([name]) => name !== "limit")) {
    return null;
  }
  if (parameters.getAll("limit").length > 1) {
    return null;
  }
  const limit = parameters.get("limit");
  if (limit !== null && !validWorkItemsLimit(limit)) {
    return null;
  }

  const canonical = new URLSearchParams();
  if (limit !== null) canonical.set("limit", String(Number(limit)));
  const query = canonical.toString();
  return query ? `?${query}` : "";
}

// The only two GET routes that accept cursor pagination: the case list and the
// per-case audit-event timeline. Both take the same {cursor?: UUID, limit?}
// pair; every other route rejects any query string.
function isCursorPaginatedListRoute(method: string, segments: string[]): boolean {
  if (method !== "GET") return false;
  const path = `/${segments.join("/")}`;
  return (
    path === "/api/v1/cases" ||
    /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/audit-events$/.test(path)
  );
}

// The work-queue list route (``GET /api/v1/work-items``) is limit-only.
function isLimitOnlyListRoute(method: string, segments: string[]): boolean {
  if (method !== "GET") return false;
  return `/${segments.join("/")}` === "/api/v1/work-items";
}

function validLimit(value: string): boolean {
  return /^\d{1,3}$/.test(value) && Number(value) >= 1 && Number(value) <= 100;
}

// Mirrors the backend bound (work_items.py, Query(ge=1, le=200)); a value the
// backend would itself reject never reaches it.
function validWorkItemsLimit(value: string): boolean {
  return /^\d{1,3}$/.test(value) && Number(value) >= 1 && Number(value) <= 200;
}

function isJsonMediaType(value: string): boolean {
  return value.split(";", 1)[0].trim().toLowerCase() === "application/json";
}

async function readBoundedBody(
  stream: ReadableStream<Uint8Array<ArrayBufferLike>> | null,
  limit: number,
): Promise<Uint8Array<ArrayBuffer> | null> {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks: Uint8Array<ArrayBufferLike>[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) {
        try {
          await reader.cancel("body-size-limit");
        } catch {
          // The limit decision is authoritative even if the peer cannot cancel.
        }
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

async function cancelBody(
  stream: ReadableStream<Uint8Array<ArrayBufferLike>> | null,
): Promise<void> {
  if (!stream || stream.locked) return;
  try {
    await stream.cancel("response-rejected");
  } catch {
    // The response is already being discarded; never expose provider details.
  }
}

function declaredBodyTooLarge(headers: Headers, limit: number): boolean {
  const raw = headers.get("content-length");
  if (raw === null || !/^\d+$/.test(raw)) return false;
  const declared = Number(raw);
  return Number.isSafeInteger(declared) && declared > limit;
}

function decodeJson(bytes: Uint8Array): unknown {
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return JSON.parse(text) as unknown;
  } catch {
    return INVALID_JSON;
  }
}

const INVALID_JSON = Symbol("INVALID_JSON");

function validateAndReconstructMutation(
  segments: string[],
  value: unknown,
): Record<string, unknown> | null {
  if (value === INVALID_JSON || !isPlainRecord(value)) return null;

  if (segments.length === 3 && segments.join("/") === "api/v1/cases") {
    if (!hasExactKeys(value, ["requestedAmount", "purpose"])) return null;
    const requestedAmount = normalizedString(value.requestedAmount, 1, 30);
    const purpose = normalizedString(value.purpose, 1, 500);
    if (
      requestedAmount === null ||
      !/^[1-9][0-9]*$/.test(requestedAmount) ||
      purpose === null ||
      looksLikeDocumentBytes(purpose)
    ) {
      return null;
    }
    return { requestedAmount, purpose };
  }

  if (
    segments.length === 5 &&
    segments[0] === "api" &&
    segments[1] === "v1" &&
    segments[2] === "cases" &&
    SAFE_ID.test(segments[3]) &&
    segments[4] === "upload-intents"
  ) {
    if (!hasExactKeys(value, ["contentType", "fileName", "sizeBytes"])) {
      return null;
    }
    const fileName = normalizedString(value.fileName, 1, 255);
    const contentType = normalizedString(value.contentType, 1, 150);
    const sizeBytes = value.sizeBytes;
    if (
      fileName === null ||
      contentType === null ||
      typeof sizeBytes !== "number" ||
      !Number.isSafeInteger(sizeBytes) ||
      sizeBytes < 1 ||
      sizeBytes > MAX_DECLARED_FILE_BYTES ||
      /[\0-\x1f\x7f/\\]/.test(fileName) ||
      looksLikeDocumentBytes(fileName)
    ) {
      return null;
    }
    const dot = fileName.lastIndexOf(".");
    const extension = dot < 0 ? "" : fileName.slice(dot).toLowerCase();
    if (ACCEPTED_UPLOAD_TYPES.get(extension) !== contentType.toLowerCase()) {
      return null;
    }
    return { fileName, contentType: contentType.toLowerCase(), sizeBytes };
  }

  if (isUploadCompletion("POST", segments)) {
    return hasExactKeys(value, []) ? {} : null;
  }

  if (isOrchestrationAdvance(segments)) {
    return hasExactKeys(value, []) ? {} : null;
  }

  // Assemble-or-get the gap-request batch, and complete intake, both take an
  // exactly-empty JSON body (the backend endpoints declare no request model).
  if (isGapRequestBatchAssemble(segments) || isIntakeCompletion(segments)) {
    return hasExactKeys(value, []) ? {} : null;
  }

  if (isGapRequestDisposition(segments)) {
    return canonicalizeGapRequestDisposition(value);
  }

  if (isRiskReviewDisposition(segments)) {
    return canonicalizeRiskDisposition(value);
  }

  if (isCreditOpsAuthorization(segments)) {
    return canonicalizeCreditOpsAuthorization(value);
  }

  if (isConfirmationSubmission(segments)) {
    return canonicalizeConfirmation(value);
  }

  // Stage 7-10 gate surfaces. The create-draft (notifications, contract
  // packages) and the conditions confirm endpoints declare NO backend request
  // model: they take an exactly-empty JSON body.
  if (
    isNotificationCreate(segments) ||
    isContractPackageCreate(segments) ||
    isConditionsConfirm(segments)
  ) {
    return hasExactKeys(value, []) ? {} : null;
  }
  if (isNotificationApprove(segments)) {
    return canonicalizeNotificationApprove(value);
  }
  if (isNotificationDeliver(segments)) {
    return canonicalizeNotificationDeliver(value);
  }
  if (isContractRedline(segments)) {
    return canonicalizeContractRedline(value);
  }
  // Contract approve + signature-authority and the stage-9 security confirm all
  // mirror the same backend {rationale} model.
  if (
    isContractApprove(segments) ||
    isContractSignatureAuthority(segments) ||
    isSecurityConfirm(segments)
  ) {
    return canonicalizeRationaleOnly(value);
  }
  if (isContractSign(segments)) {
    return canonicalizeContractSign(value);
  }
  if (isSecurityInterestCreate(segments)) {
    return canonicalizeSecurityInterest(value);
  }
  if (isSecurityItemAdd(segments)) {
    return canonicalizeAddPerfectionItem(value);
  }
  if (isSecurityItemTransition(segments)) {
    return canonicalizeTransitionPerfectionItem(value);
  }
  if (isConditionCreate(segments)) {
    return canonicalizeCreateCondition(value);
  }
  if (isConditionTransition(segments)) {
    return canonicalizeTransitionCondition(value);
  }

  // Stage 11-14 post-credit surfaces. The disbursement gate writes (validate /
  // authorize / execute), the settlement confirm and the recovery strategy
  // approval all declare NO backend request model: they take an exactly-empty
  // JSON body.
  if (
    isDisbursementValidate(segments) ||
    isDisbursementAuthorize(segments) ||
    isDisbursementExecute(segments) ||
    isSettlementConfirm(segments) ||
    isRecoveryApproveStrategy(segments)
  ) {
    return hasExactKeys(value, []) ? {} : null;
  }
  if (isDisbursementCreate(segments)) {
    return canonicalizeCreateDisbursement(value);
  }
  if (isDisbursementReconcile(segments)) {
    return canonicalizeReconcile(value);
  }
  if (isObligationsCreate(segments)) {
    return canonicalizeCreateObligations(value);
  }
  if (isObservationCreate(segments)) {
    return canonicalizeCreateObservation(value);
  }
  if (isCovenantCreate(segments)) {
    return canonicalizeCreateCovenant(value);
  }
  if (isCovenantTest(segments)) {
    return canonicalizeRunCovenantTest(value);
  }
  if (isAlertDisposition(segments)) {
    return canonicalizeAlertDisposition(value);
  }
  if (isRepaymentCreate(segments)) {
    return canonicalizeCreateFacility(value);
  }
  if (isRepaymentEvent(segments)) {
    return canonicalizeRecordEvent(value);
  }
  if (isRepaymentNote(segments)) {
    return canonicalizeCreateNote(value);
  }
  if (isSettlementCheck(segments)) {
    return canonicalizeSettlementCheck(value);
  }
  if (isRecoveryOpen(segments)) {
    return canonicalizeOpenRecovery(value);
  }

  return null;
}

function isOrchestrationAdvance(segments: string[]): boolean {
  const path = `/${segments.join("/")}`;
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/orchestration\/advance$/.test(path);
}

function isRiskReviewDisposition(segments: string[]): boolean {
  const path = `/${segments.join("/")}`;
  return (
    /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/risk-review\/disposition$/.test(path) ||
    /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/risk-review\/challenges\/[A-Za-z0-9_-]+\/disposition$/.test(
      path,
    )
  );
}

function canonicalizeRiskDisposition(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["dispositionType", "rationale"])) return null;
  const dispositionType = normalizedString(value.dispositionType, 1, 50);
  const rationale = normalizedString(value.rationale, 1, 4000);
  if (
    dispositionType === null ||
    !/^[A-Z_]+$/.test(dispositionType) ||
    rationale === null ||
    looksLikeDocumentBytes(rationale)
  ) {
    return null;
  }
  return { dispositionType, rationale };
}

function isCreditOpsAuthorization(segments: string[]): boolean {
  const path = `/${segments.join("/")}`;
  return (
    /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/credit-ops\/actions\/[A-Za-z0-9_-]+\/authorize$/.test(
      path,
    ) ||
    /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/credit-ops\/document-requests\/[A-Za-z0-9_-]+\/approve$/.test(
      path,
    )
  );
}

// Mirrors backend RecordAuthorizationRequest: exactly {rationale}, 1-4000 chars,
// extra keys forbidden (credit_ops.py, model_config extra="forbid").
function canonicalizeCreditOpsAuthorization(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["rationale"])) return null;
  const rationale = normalizedString(value.rationale, 1, 4000);
  if (rationale === null || looksLikeDocumentBytes(rationale)) return null;
  return { rationale };
}

function isGapRequestBatchAssemble(segments: string[]): boolean {
  const path = `/${segments.join("/")}`;
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/gap-request-batches$/.test(path);
}

function isGapRequestDisposition(segments: string[]): boolean {
  const path = `/${segments.join("/")}`;
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/gap-request-batches\/[A-Za-z0-9_-]+\/disposition$/.test(
    path,
  );
}

function isIntakeCompletion(segments: string[]): boolean {
  const path = `/${segments.join("/")}`;
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/intake-completion$/.test(path);
}

// Closed enums mirrored from services/.../domain/gap_request_batches.py.
const BATCH_DISPOSITION_TYPES = new Set([
  "APPROVED_ALL",
  "APPROVED_WITH_CHANGES",
  "REJECTED",
  "NO_OUTBOUND_REQUESTS",
]);
const ITEM_DISPOSITIONS = new Set(["APPROVED", "REMOVED", "EDITED"]);
const MAX_GAP_ITEM_ENTRIES = 500;
const MAX_EDITED_TEXT = 2000;

// Mirrors backend RecordBatchDispositionRequest (gap_requests.py, extra=forbid):
// {dispositionType, rationale} are required; {itemDispositions, editedTexts} are
// optional maps whose keys are batch-item UUIDs and whose values come from the
// closed enums (item dispositions) or are plain replacement text (edited texts).
function canonicalizeGapRequestDisposition(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const keys = Object.keys(value);
  const allowed = new Set(["dispositionType", "rationale", "itemDispositions", "editedTexts"]);
  if (keys.some((key) => !allowed.has(key))) return null;
  if (!("dispositionType" in value) || !("rationale" in value)) return null;

  const dispositionType = normalizedString(value.dispositionType, 1, 50);
  if (dispositionType === null || !BATCH_DISPOSITION_TYPES.has(dispositionType)) {
    return null;
  }
  const rationale = normalizedString(value.rationale, 1, 4000);
  if (rationale === null || looksLikeDocumentBytes(rationale)) return null;

  const canonical: Record<string, unknown> = { dispositionType, rationale };

  if ("itemDispositions" in value) {
    const itemDispositions = canonicalizeUuidMap(value.itemDispositions, (entryValue) =>
      typeof entryValue === "string" && ITEM_DISPOSITIONS.has(entryValue) ? entryValue : null,
    );
    if (itemDispositions === null) return null;
    canonical.itemDispositions = itemDispositions;
  }

  if ("editedTexts" in value) {
    const editedTexts = canonicalizeUuidMap(value.editedTexts, (entryValue) => {
      const text = normalizedString(entryValue, 1, MAX_EDITED_TEXT);
      return text !== null && !looksLikeDocumentBytes(text) ? text : null;
    });
    if (editedTexts === null) return null;
    canonical.editedTexts = editedTexts;
  }

  return canonical;
}

// Reconstructs a {uuid: value} map field-by-field: every key must be a UUID
// (lower-cased so two spellings can never collide into one backend key), and
// every value must pass the caller's closed-enum / text validator.
function canonicalizeUuidMap(
  value: unknown,
  validateValue: (entryValue: unknown) => string | null,
): Record<string, string> | null {
  if (!isPlainRecord(value)) return null;
  const entries = Object.entries(value);
  if (entries.length > MAX_GAP_ITEM_ENTRIES) return null;
  const canonical: Record<string, string> = {};
  const seen = new Set<string>();
  for (const [rawKey, rawValue] of entries) {
    if (!UUID.test(rawKey)) return null;
    const key = rawKey.toLowerCase();
    if (seen.has(key)) return null;
    seen.add(key);
    const validated = validateValue(rawValue);
    if (validated === null) return null;
    canonical[key] = validated;
  }
  return canonical;
}

function isConfirmationSubmission(segments: string[]): boolean {
  return (
    segments.length === 5 &&
    segments[0] === "api" &&
    segments[1] === "v1" &&
    segments[2] === "documents" &&
    SAFE_ID.test(segments[3]) &&
    segments[4] === "confirmations"
  );
}

function canonicalizeConfirmation(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["dispositions", "expectedDocumentVersion"])) {
    return null;
  }
  const expectedDocumentVersion = value.expectedDocumentVersion;
  if (
    typeof expectedDocumentVersion !== "number" ||
    !Number.isInteger(expectedDocumentVersion) ||
    expectedDocumentVersion < 1 ||
    expectedDocumentVersion > MAX_DOCUMENT_VERSION
  ) {
    return null;
  }

  const dispositions = value.dispositions;
  if (
    !Array.isArray(dispositions) ||
    dispositions.length < 1 ||
    dispositions.length > MAX_DISPOSITIONS
  ) {
    return null;
  }

  const seenCandidateIds = new Set<string>();
  const canonicalDispositions: Record<string, unknown>[] = [];
  for (const entry of dispositions) {
    if (!isPlainRecord(entry)) return null;
    const candidateId = entry.candidateId;
    if (
      typeof candidateId !== "string" ||
      candidateId.length < 1 ||
      candidateId.length > 64 ||
      !SAFE_ID.test(candidateId)
    ) {
      return null;
    }
    if (seenCandidateIds.has(candidateId)) return null;
    seenCandidateIds.add(candidateId);

    const disposition = entry.disposition;
    if (typeof disposition !== "string" || !FACT_DISPOSITIONS.has(disposition)) {
      return null;
    }

    if (disposition === "CORRECTED") {
      if (
        !hasExactKeys(entry, [
          "candidateId",
          "correctedValue",
          "disposition",
          "rationale",
        ])
      ) {
        return null;
      }
      const correctedValue = normalizedString(entry.correctedValue, 1, 500);
      const rationale = normalizedString(entry.rationale, 1, 1000);
      if (
        correctedValue === null ||
        looksLikeDocumentBytes(correctedValue) ||
        rationale === null ||
        looksLikeDocumentBytes(rationale)
      ) {
        return null;
      }
      canonicalDispositions.push({
        candidateId,
        disposition,
        correctedValue,
        rationale,
      });
    } else {
      if (!hasExactKeys(entry, ["candidateId", "disposition"])) return null;
      canonicalDispositions.push({ candidateId, disposition });
    }
  }

  return { expectedDocumentVersion, dispositions: canonicalDispositions };
}

// --- Stage 7-10 route predicates + body reconstruction --------------------
//
// Backend truth mirrored here (extra="forbid" on every model):
//   services/.../api/notifications.py, contract_packages.py,
//   security_interests.py, conditions.py. Each canonicalizer rebuilds the body
//   field-by-field so an undeclared key, a wrong type, or a smuggled document
//   byte-stream fails closed before reaching upstream.

//: Closed PROPOSED synthetic taxonomies mirrored from the stage-9/10 domains.
const SECURITY_ASSET_KINDS = new Set([
  "REAL_ESTATE",
  "VEHICLE",
  "DEPOSIT",
  "RECEIVABLE",
  "OTHER",
]);
const PERFECTION_STATUSES = new Set([
  "PENDING",
  "EVIDENCE_ATTACHED",
  "COMPLETED",
  "NOT_REQUIRED_BY_HUMAN",
  "EXPIRED",
]);
const CONDITION_STATUSES = new Set([
  "PENDING",
  "EVIDENCE_SUBMITTED",
  "VERIFIED",
  "FAILED",
  "WAIVER_REQUESTED",
  "WAIVED_BY_HUMAN",
  "SUPERSEDED",
  "NOT_APPLICABLE_BY_HUMAN",
]);
const MAX_EVIDENCE_REFS = 50;
const MAX_EVIDENCE_REF_LEN = 500;
const MAX_SIGNER_NAMES = 25;
const MAX_SIGNER_NAME_LEN = 500;
const MAX_CONTRACT_CONTENT = 200_000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function pathOf(segments: string[]): string {
  return `/${segments.join("/")}`;
}

function isNotificationCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/notifications$/.test(pathOf(segments));
}

function isNotificationApprove(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/notifications\/approve$/.test(
    pathOf(segments),
  );
}

function isNotificationDeliver(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/notifications\/deliver$/.test(
    pathOf(segments),
  );
}

function isContractPackageCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages$/.test(pathOf(segments));
}

function isContractRedline(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages\/redlines$/.test(
    pathOf(segments),
  );
}

function isContractApprove(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages\/approve$/.test(
    pathOf(segments),
  );
}

function isContractSignatureAuthority(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages\/signature-authority$/.test(
    pathOf(segments),
  );
}

function isContractSign(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages\/sign$/.test(
    pathOf(segments),
  );
}

function isSecurityInterestCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests$/.test(pathOf(segments));
}

function isSecurityConfirm(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests\/confirm$/.test(
    pathOf(segments),
  );
}

function isSecurityItemAdd(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests\/[A-Za-z0-9_-]+\/items$/.test(
    pathOf(segments),
  );
}

function isSecurityItemTransition(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests\/items\/[A-Za-z0-9_-]+\/transition$/.test(
    pathOf(segments),
  );
}

function isConditionCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conditions$/.test(pathOf(segments));
}

function isConditionsConfirm(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conditions\/confirm$/.test(pathOf(segments));
}

function isConditionTransition(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conditions\/[A-Za-z0-9_-]+\/transition$/.test(
    pathOf(segments),
  );
}

// An ISO calendar date (YYYY-MM-DD) that is a real day; anything else fails.
function normalizedDate(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!ISO_DATE.test(trimmed)) return null;
  const [year, month, day] = trimmed.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }
  return trimmed;
}

// A bounded array of non-empty, non-byte-carrying strings (evidence refs,
// signer names). Reconstructed element-by-element; any bad entry fails closed.
function canonicalizeStringArray(
  value: unknown,
  maxCount: number,
  maxLen: number,
): string[] | null {
  if (!Array.isArray(value) || value.length > maxCount) return null;
  const result: string[] = [];
  for (const entry of value) {
    const text = normalizedString(entry, 1, maxLen);
    if (text === null || looksLikeDocumentBytes(text)) return null;
    result.push(text);
  }
  return result;
}

// Mirrors ApproveNotificationRequest: exactly {draftId, rationale}. The draftId
// is lower-cased so two UUID spellings can never disagree with the backend.
function canonicalizeNotificationApprove(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["draftId", "rationale"])) return null;
  const draftId = typeof value.draftId === "string" ? value.draftId.trim().toLowerCase() : "";
  if (!UUID.test(draftId)) return null;
  const rationale = normalizedString(value.rationale, 1, 4000);
  if (rationale === null || looksLikeDocumentBytes(rationale)) return null;
  return { draftId, rationale };
}

// Mirrors DeliverNotificationRequest: an OPTIONAL {receiptNote}. An empty body
// (no note) is valid; the backend defaults the labelled mock note.
function canonicalizeNotificationDeliver(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (Object.keys(value).some((key) => key !== "receiptNote")) return null;
  if (!("receiptNote" in value)) return {};
  const receiptNote = normalizedString(value.receiptNote, 1, 4000);
  if (receiptNote === null || looksLikeDocumentBytes(receiptNote)) return null;
  return { receiptNote };
}

// Mirrors AddRedlineRequest: exactly {changeNote, changedContent}. The redlined
// content is a large free-text field (<=200000 chars) that may legitimately
// embed content hashes, so it is NOT run through the base64 heuristic (which
// would false-positive on a 64-char hex hash); it is still length- and
// control-char-bounded by normalizedString.
function canonicalizeContractRedline(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["changeNote", "changedContent"])) return null;
  const changeNote = normalizedString(value.changeNote, 1, 4000);
  const changedContent = normalizedString(value.changedContent, 1, MAX_CONTRACT_CONTENT);
  if (changeNote === null || looksLikeDocumentBytes(changeNote)) return null;
  if (changedContent === null) return null;
  return { changeNote, changedContent };
}

// Mirrors ApproveRequest / SignatureAuthorityRequest / ConfirmRequest: exactly
// {rationale}, 1-4000 chars, no smuggled bytes.
function canonicalizeRationaleOnly(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["rationale"])) return null;
  const rationale = normalizedString(value.rationale, 1, 4000);
  if (rationale === null || looksLikeDocumentBytes(rationale)) return null;
  return { rationale };
}

// Mirrors SignRequest: {signerNames (>=1), evidenceNote?}.
function canonicalizeContractSign(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (Object.keys(value).some((key) => key !== "signerNames" && key !== "evidenceNote")) {
    return null;
  }
  if (!("signerNames" in value)) return null;
  const signerNames = canonicalizeStringArray(
    value.signerNames,
    MAX_SIGNER_NAMES,
    MAX_SIGNER_NAME_LEN,
  );
  if (signerNames === null || signerNames.length < 1) return null;
  const canonical: Record<string, unknown> = { signerNames };
  if ("evidenceNote" in value) {
    const evidenceNote = normalizedString(value.evidenceNote, 1, 4000);
    if (evidenceNote === null || looksLikeDocumentBytes(evidenceNote)) return null;
    canonical.evidenceNote = evidenceNote;
  }
  return canonical;
}

// Mirrors CreateInterestRequest.
function canonicalizeSecurityInterest(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set([
    "assetDescription",
    "assetKind",
    "ownerName",
    "valuationReference",
    "notes",
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const assetDescription = normalizedString(value.assetDescription, 1, 2000);
  const assetKind = normalizedString(value.assetKind, 1, 32);
  if (assetDescription === null || looksLikeDocumentBytes(assetDescription)) return null;
  if (assetKind === null || !SECURITY_ASSET_KINDS.has(assetKind)) return null;
  const canonical: Record<string, unknown> = { assetDescription, assetKind };
  const optionalText: [string, number][] = [
    ["ownerName", 500],
    ["valuationReference", 500],
    ["notes", 4000],
  ];
  for (const [key, max] of optionalText) {
    if (!(key in value)) continue;
    const text = normalizedString(value[key], 1, max);
    if (text === null || looksLikeDocumentBytes(text)) return null;
    canonical[key] = text;
  }
  return canonical;
}

// Mirrors AddItemRequest.
function canonicalizeAddPerfectionItem(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set([
    "requirement",
    "evidenceRefs",
    "filingReference",
    "effectiveDate",
    "expiryDate",
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const requirement = normalizedString(value.requirement, 1, 2000);
  if (requirement === null || looksLikeDocumentBytes(requirement)) return null;
  const canonical: Record<string, unknown> = { requirement };
  return finishPerfectionOptionals(value, canonical);
}

// Mirrors TransitionItemRequest.
function canonicalizeTransitionPerfectionItem(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set([
    "toStatus",
    "rationale",
    "evidenceRefs",
    "filingReference",
    "effectiveDate",
    "expiryDate",
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const toStatus = normalizedString(value.toStatus, 1, 32);
  if (toStatus === null || !PERFECTION_STATUSES.has(toStatus)) return null;
  const canonical: Record<string, unknown> = { toStatus };
  if ("rationale" in value) {
    const rationale = normalizedString(value.rationale, 1, 4000);
    if (rationale === null || looksLikeDocumentBytes(rationale)) return null;
    canonical.rationale = rationale;
  }
  return finishPerfectionOptionals(value, canonical);
}

// The shared optional tail of the two perfection-item bodies (evidenceRefs,
// filingReference, effectiveDate, expiryDate).
function finishPerfectionOptionals(
  value: Record<string, unknown>,
  canonical: Record<string, unknown>,
): Record<string, unknown> | null {
  if ("evidenceRefs" in value) {
    const evidenceRefs = canonicalizeStringArray(
      value.evidenceRefs,
      MAX_EVIDENCE_REFS,
      MAX_EVIDENCE_REF_LEN,
    );
    if (evidenceRefs === null) return null;
    canonical.evidenceRefs = evidenceRefs;
  }
  if ("filingReference" in value) {
    const filingReference = normalizedString(value.filingReference, 1, 500);
    if (filingReference === null || looksLikeDocumentBytes(filingReference)) return null;
    canonical.filingReference = filingReference;
  }
  for (const key of ["effectiveDate", "expiryDate"]) {
    if (!(key in value)) continue;
    const date = normalizedDate(value[key]);
    if (date === null) return null;
    canonical[key] = date;
  }
  return canonical;
}

// Mirrors CreateConditionRequest.
function canonicalizeCreateCondition(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set(["conditionText", "owner", "dueDate"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const conditionText = normalizedString(value.conditionText, 1, 4000);
  if (conditionText === null || looksLikeDocumentBytes(conditionText)) return null;
  const canonical: Record<string, unknown> = { conditionText };
  if ("owner" in value) {
    const owner = normalizedString(value.owner, 1, 400);
    if (owner === null || looksLikeDocumentBytes(owner)) return null;
    canonical.owner = owner;
  }
  if ("dueDate" in value) {
    const dueDate = normalizedDate(value.dueDate);
    if (dueDate === null) return null;
    canonical.dueDate = dueDate;
  }
  return canonical;
}

// Mirrors TransitionConditionRequest.
function canonicalizeTransitionCondition(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set(["toStatus", "rationale", "evidenceRefs"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const toStatus = normalizedString(value.toStatus, 1, 64);
  if (toStatus === null || !CONDITION_STATUSES.has(toStatus)) return null;
  const canonical: Record<string, unknown> = { toStatus };
  if ("rationale" in value) {
    const rationale = normalizedString(value.rationale, 1, 4000);
    if (rationale === null || looksLikeDocumentBytes(rationale)) return null;
    canonical.rationale = rationale;
  }
  if ("evidenceRefs" in value) {
    const evidenceRefs = canonicalizeStringArray(
      value.evidenceRefs,
      MAX_EVIDENCE_REFS,
      MAX_EVIDENCE_REF_LEN,
    );
    if (evidenceRefs === null) return null;
    canonical.evidenceRefs = evidenceRefs;
  }
  return canonical;
}

// --- Stage 11-14 post-credit route predicates + body reconstruction ---------
//
// Backend truth mirrored here (extra="forbid" on every request model):
//   services/.../api/{disbursements,monitoring,repayments,settlement_recovery}.py.
// Each body is rebuilt field-by-field so an undeclared key, a wrong type, a
// smuggled document byte-stream, or a non-decimal amount fails closed before it
// reaches upstream. Money crosses as an exact-decimal STRING (never a float);
// integers are bounded to the backend's own range.

const DECIMAL = /^-?\d+(\.\d+)?$/;
const ISO_DATETIME =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/;
const MAX_AMOUNT_LEN = 64;
const MAX_SYNTHETIC_INT = 1_000_000;
const MAX_RECOVERY_OPTIONS = 20;

//: Closed PROPOSED synthetic taxonomies mirrored from the stage 11-14 domains.
const RECONCILE_OUTCOMES = new Set(["CONFIRMED_EXECUTED", "CONFIRMED_NOT_EXECUTED"]);
const OBLIGATION_FREQUENCIES = new Set(["MONTHLY", "QUARTERLY"]);
const COMPARISON_OPERATORS = new Set(["GTE", "GT", "LTE", "LT", "EQ"]);
const ALERT_DISPOSITION_TARGETS = new Set([
  "ACKNOWLEDGED",
  "ESCALATED",
  "DISMISSED_BY_HUMAN",
]);
const REPAYMENT_STYLES = new Set(["EQUAL_PRINCIPAL", "BALLOON"]);
const EVENT_KINDS = new Set(["PAYMENT", "REVERSAL"]);
const COLLECTION_NOTE_KINDS = new Set(["OBSERVATION", "PROPOSED_ACTION"]);

// An exact-decimal money string (optionally signed); the backend parses it as a
// Decimal. Bounded by the backend's own max_length. NOT run through the base64
// heuristic (a long integer would false-positive); the pattern is already exact.
function normalizedDecimal(value: unknown, maxLen: number): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length < 1 || trimmed.length > maxLen || !DECIMAL.test(trimmed)) {
    return null;
  }
  return trimmed;
}

// A bounded integer forwarded verbatim (count, term, version, exception count).
function boundedInt(value: unknown, minimum: number, maximum: number): number | null {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    return null;
  }
  return value;
}

// An ISO-8601 datetime naming a real instant; forwarded verbatim so no timezone
// re-normalization ever shifts the caller's separated effective/observed stamps.
function normalizedDateTime(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!ISO_DATETIME.test(trimmed) || Number.isNaN(new Date(trimmed).getTime())) {
    return null;
  }
  return trimmed;
}

// A lower-cased UUID (two spellings can never disagree with the backend key).
function normalizedUuid(value: unknown): string | null {
  const candidate = typeof value === "string" ? value.trim().toLowerCase() : "";
  return UUID.test(candidate) ? candidate : null;
}

// -- disbursements (stage 11) --

function isDisbursementCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements$/.test(
    pathOf(segments),
  );
}

function isDisbursementValidate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements\/[A-Za-z0-9_-]+\/validate$/.test(
    pathOf(segments),
  );
}

function isDisbursementAuthorize(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements\/[A-Za-z0-9_-]+\/authorize$/.test(
    pathOf(segments),
  );
}

function isDisbursementExecute(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements\/[A-Za-z0-9_-]+\/execute$/.test(
    pathOf(segments),
  );
}

function isDisbursementReconcile(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements\/[A-Za-z0-9_-]+\/reconcile$/.test(
    pathOf(segments),
  );
}

// Mirrors CreateDisbursementRequest: required {beneficiaryRef, accountRef};
// optional exact-decimal {amount} (<=40) and {currency} (<=8).
function canonicalizeCreateDisbursement(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set(["amount", "currency", "beneficiaryRef", "accountRef"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const beneficiaryRef = normalizedString(value.beneficiaryRef, 1, 400);
  const accountRef = normalizedString(value.accountRef, 1, 400);
  if (beneficiaryRef === null || looksLikeDocumentBytes(beneficiaryRef)) return null;
  if (accountRef === null || looksLikeDocumentBytes(accountRef)) return null;
  const canonical: Record<string, unknown> = { beneficiaryRef, accountRef };
  if ("amount" in value) {
    const amount = normalizedDecimal(value.amount, 40);
    if (amount === null) return null;
    canonical.amount = amount;
  }
  if ("currency" in value) {
    const currency = normalizedString(value.currency, 1, 8);
    if (currency === null || looksLikeDocumentBytes(currency)) return null;
    canonical.currency = currency;
  }
  return canonical;
}

// Mirrors ReconcileRequest: {outcome ∈ reconciliation set, rationale}. Only the
// two human-reconciliation outcomes are accepted (never an adapter result).
function canonicalizeReconcile(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["outcome", "rationale"])) return null;
  const outcome = normalizedString(value.outcome, 1, 64);
  if (outcome === null || !RECONCILE_OUTCOMES.has(outcome)) return null;
  const rationale = normalizedString(value.rationale, 1, 4000);
  if (rationale === null || looksLikeDocumentBytes(rationale)) return null;
  return { outcome, rationale };
}

// -- monitoring (stage 12) --

function isObligationsCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/obligations$/.test(
    pathOf(segments),
  );
}

function isObservationCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/observations$/.test(
    pathOf(segments),
  );
}

function isCovenantCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/covenants$/.test(
    pathOf(segments),
  );
}

function isCovenantTest(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/covenants\/[A-Za-z0-9_-]+\/test$/.test(
    pathOf(segments),
  );
}

function isAlertDisposition(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/alerts\/[A-Za-z0-9_-]+\/disposition$/.test(
    pathOf(segments),
  );
}

// Mirrors CreateObligationsRequest: {frequency, requirementText, fromDate, count}.
function canonicalizeCreateObligations(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["frequency", "requirementText", "fromDate", "count"])) {
    return null;
  }
  const frequency = normalizedString(value.frequency, 1, 32);
  if (frequency === null || !OBLIGATION_FREQUENCIES.has(frequency)) return null;
  const requirementText = normalizedString(value.requirementText, 1, 4000);
  if (requirementText === null || looksLikeDocumentBytes(requirementText)) return null;
  const fromDate = normalizedDate(value.fromDate);
  if (fromDate === null) return null;
  const count = boundedInt(value.count, 1, 120);
  if (count === null) return null;
  return { frequency, requirementText, fromDate, count };
}

// Mirrors CreateObservationRequest: required {observationType, body, effectiveAt,
// observedAt}; optional {obligationId (UUID), evidenceRefs}.
function canonicalizeCreateObservation(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set([
    "obligationId",
    "observationType",
    "body",
    "effectiveAt",
    "observedAt",
    "evidenceRefs",
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const observationType = normalizedString(value.observationType, 1, 200);
  if (observationType === null || looksLikeDocumentBytes(observationType)) return null;
  const body = normalizedString(value.body, 1, 8000);
  if (body === null || looksLikeDocumentBytes(body)) return null;
  const effectiveAt = normalizedDateTime(value.effectiveAt);
  const observedAt = normalizedDateTime(value.observedAt);
  if (effectiveAt === null || observedAt === null) return null;
  const canonical: Record<string, unknown> = {
    observationType,
    body,
    effectiveAt,
    observedAt,
  };
  if ("obligationId" in value) {
    const obligationId = normalizedUuid(value.obligationId);
    if (obligationId === null) return null;
    canonical.obligationId = obligationId;
  }
  if ("evidenceRefs" in value) {
    const evidenceRefs = canonicalizeStringArray(
      value.evidenceRefs,
      MAX_EVIDENCE_REFS,
      MAX_EVIDENCE_REF_LEN,
    );
    if (evidenceRefs === null) return null;
    canonical.evidenceRefs = evidenceRefs;
  }
  return canonical;
}

// Mirrors CreateCovenantRequest: {name, metricKey, operator, thresholdValue,
// thresholdVersion}.
function canonicalizeCreateCovenant(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (
    !hasExactKeys(value, [
      "name",
      "metricKey",
      "operator",
      "thresholdValue",
      "thresholdVersion",
    ])
  ) {
    return null;
  }
  const name = normalizedString(value.name, 1, 400);
  if (name === null || looksLikeDocumentBytes(name)) return null;
  const metricKey = normalizedString(value.metricKey, 1, 200);
  if (metricKey === null || looksLikeDocumentBytes(metricKey)) return null;
  const operator = normalizedString(value.operator, 1, 8);
  if (operator === null || !COMPARISON_OPERATORS.has(operator)) return null;
  const thresholdValue = normalizedDecimal(value.thresholdValue, MAX_AMOUNT_LEN);
  if (thresholdValue === null) return null;
  const thresholdVersion = boundedInt(value.thresholdVersion, 1, MAX_SYNTHETIC_INT);
  if (thresholdVersion === null) return null;
  return { name, metricKey, operator, thresholdValue, thresholdVersion };
}

// Mirrors RunCovenantTestRequest: {numerator, denominator?}.
function canonicalizeRunCovenantTest(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (Object.keys(value).some((key) => key !== "numerator" && key !== "denominator")) {
    return null;
  }
  if (!("numerator" in value)) return null;
  const numerator = normalizedDecimal(value.numerator, MAX_AMOUNT_LEN);
  if (numerator === null) return null;
  const canonical: Record<string, unknown> = { numerator };
  if ("denominator" in value) {
    const denominator = normalizedDecimal(value.denominator, MAX_AMOUNT_LEN);
    if (denominator === null) return null;
    canonical.denominator = denominator;
  }
  return canonical;
}

// Mirrors DisposeAlertRequest: {toStatus ∈ disposition targets, rationale}. OPEN
// is never a target (a deterministic rule alone creates it).
function canonicalizeAlertDisposition(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!hasExactKeys(value, ["toStatus", "rationale"])) return null;
  const toStatus = normalizedString(value.toStatus, 1, 64);
  if (toStatus === null || !ALERT_DISPOSITION_TARGETS.has(toStatus)) return null;
  const rationale = normalizedString(value.rationale, 1, 4000);
  if (rationale === null || looksLikeDocumentBytes(rationale)) return null;
  return { toStatus, rationale };
}

// -- repayments (stage 13) --

function isRepaymentCreate(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/repayments$/.test(pathOf(segments));
}

function isRepaymentEvent(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/repayments\/[A-Za-z0-9_-]+\/events$/.test(
    pathOf(segments),
  );
}

function isRepaymentNote(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/repayments\/[A-Za-z0-9_-]+\/notes$/.test(
    pathOf(segments),
  );
}

// Mirrors CreateFacilityRequest.
function canonicalizeCreateFacility(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set([
    "principal",
    "annualRatePercent",
    "termMonths",
    "repaymentStyle",
    "firstPaymentDate",
    "periodicFee",
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const principal = normalizedDecimal(value.principal, MAX_AMOUNT_LEN);
  const annualRatePercent = normalizedDecimal(value.annualRatePercent, MAX_AMOUNT_LEN);
  if (principal === null || annualRatePercent === null) return null;
  const termMonths = boundedInt(value.termMonths, 1, 600);
  if (termMonths === null) return null;
  const repaymentStyle = normalizedString(value.repaymentStyle, 1, 32);
  if (repaymentStyle === null || !REPAYMENT_STYLES.has(repaymentStyle)) return null;
  const firstPaymentDate = normalizedDate(value.firstPaymentDate);
  if (firstPaymentDate === null) return null;
  const canonical: Record<string, unknown> = {
    principal,
    annualRatePercent,
    termMonths,
    repaymentStyle,
    firstPaymentDate,
  };
  if ("periodicFee" in value) {
    const periodicFee = normalizedDecimal(value.periodicFee, MAX_AMOUNT_LEN);
    if (periodicFee === null) return null;
    canonical.periodicFee = periodicFee;
  }
  return canonical;
}

// Mirrors RecordEventRequest.
function canonicalizeRecordEvent(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set([
    "kind",
    "amount",
    "externalReference",
    "effectiveDate",
    "reversedEventId",
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const kind = normalizedString(value.kind, 1, 32);
  if (kind === null || !EVENT_KINDS.has(kind)) return null;
  const amount = normalizedDecimal(value.amount, MAX_AMOUNT_LEN);
  if (amount === null) return null;
  const externalReference = normalizedString(value.externalReference, 1, 200);
  if (externalReference === null || looksLikeDocumentBytes(externalReference)) return null;
  const effectiveDate = normalizedDate(value.effectiveDate);
  if (effectiveDate === null) return null;
  const canonical: Record<string, unknown> = {
    kind,
    amount,
    externalReference,
    effectiveDate,
  };
  if ("reversedEventId" in value) {
    const reversedEventId = normalizedUuid(value.reversedEventId);
    if (reversedEventId === null) return null;
    canonical.reversedEventId = reversedEventId;
  }
  return canonical;
}

// Mirrors CreateNoteRequest.
function canonicalizeCreateNote(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  const allowed = new Set(["noteKind", "noteText", "proposedAction"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  const noteKind = normalizedString(value.noteKind, 1, 32);
  if (noteKind === null || !COLLECTION_NOTE_KINDS.has(noteKind)) return null;
  const noteText = normalizedString(value.noteText, 1, 4000);
  if (noteText === null || looksLikeDocumentBytes(noteText)) return null;
  const canonical: Record<string, unknown> = { noteKind, noteText };
  if ("proposedAction" in value) {
    const proposedAction = normalizedString(value.proposedAction, 1, 400);
    if (proposedAction === null || looksLikeDocumentBytes(proposedAction)) return null;
    canonical.proposedAction = proposedAction;
  }
  return canonical;
}

// -- settlement + recovery (stage 14) --

function isSettlementCheck(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/settlement\/check$/.test(pathOf(segments));
}

function isSettlementConfirm(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/settlement\/confirm$/.test(pathOf(segments));
}

function isRecoveryOpen(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/recovery$/.test(pathOf(segments));
}

function isRecoveryApproveStrategy(segments: string[]): boolean {
  return /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/recovery\/[A-Za-z0-9_-]+\/approve-strategy$/.test(
    pathOf(segments),
  );
}

// Mirrors SettlementCheckRequest.
function canonicalizeSettlementCheck(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (
    !hasExactKeys(value, [
      "outstandingPrincipal",
      "outstandingInterest",
      "outstandingFees",
      "openExceptionCount",
    ])
  ) {
    return null;
  }
  const outstandingPrincipal = normalizedDecimal(value.outstandingPrincipal, MAX_AMOUNT_LEN);
  const outstandingInterest = normalizedDecimal(value.outstandingInterest, MAX_AMOUNT_LEN);
  const outstandingFees = normalizedDecimal(value.outstandingFees, MAX_AMOUNT_LEN);
  if (
    outstandingPrincipal === null ||
    outstandingInterest === null ||
    outstandingFees === null
  ) {
    return null;
  }
  const openExceptionCount = boundedInt(value.openExceptionCount, 0, MAX_SYNTHETIC_INT);
  if (openExceptionCount === null) return null;
  return {
    outstandingPrincipal,
    outstandingInterest,
    outstandingFees,
    openExceptionCount,
  };
}

// Mirrors OpenRecoveryRequest, including its nested options (>=1).
function canonicalizeOpenRecovery(
  value: Record<string, unknown>,
): Record<string, unknown> | null {
  if (
    !hasExactKeys(value, [
      "outstandingTotal",
      "periodsInShortfall",
      "triggerSummary",
      "escalationRationale",
      "evidenceRefs",
      "options",
    ])
  ) {
    return null;
  }
  const outstandingTotal = normalizedDecimal(value.outstandingTotal, MAX_AMOUNT_LEN);
  if (outstandingTotal === null) return null;
  const periodsInShortfall = boundedInt(value.periodsInShortfall, 0, MAX_SYNTHETIC_INT);
  if (periodsInShortfall === null) return null;
  const triggerSummary = normalizedString(value.triggerSummary, 1, 4000);
  if (triggerSummary === null || looksLikeDocumentBytes(triggerSummary)) return null;
  const escalationRationale = normalizedString(value.escalationRationale, 1, 4000);
  if (escalationRationale === null || looksLikeDocumentBytes(escalationRationale)) return null;
  const evidenceRefs = canonicalizeStringArray(
    value.evidenceRefs,
    MAX_EVIDENCE_REFS,
    MAX_EVIDENCE_REF_LEN,
  );
  if (evidenceRefs === null || evidenceRefs.length < 1) return null;
  const options = canonicalizeRecoveryOptions(value.options);
  if (options === null) return null;
  return {
    outstandingTotal,
    periodsInShortfall,
    triggerSummary,
    escalationRationale,
    evidenceRefs,
    options,
  };
}

// Mirrors the RecoveryOptionRequest tuple (>=1): required {label, description,
// consequences}; optional {dependencies}. Rebuilt option-by-option.
function canonicalizeRecoveryOptions(
  value: unknown,
): Record<string, unknown>[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_RECOVERY_OPTIONS) {
    return null;
  }
  const result: Record<string, unknown>[] = [];
  for (const entry of value) {
    if (!isPlainRecord(entry)) return null;
    const allowed = new Set(["label", "description", "consequences", "dependencies"]);
    if (Object.keys(entry).some((key) => !allowed.has(key))) return null;
    const label = normalizedString(entry.label, 1, 400);
    const description = normalizedString(entry.description, 1, 4000);
    const consequences = normalizedString(entry.consequences, 1, 4000);
    if (label === null || looksLikeDocumentBytes(label)) return null;
    if (description === null || looksLikeDocumentBytes(description)) return null;
    if (consequences === null || looksLikeDocumentBytes(consequences)) return null;
    const option: Record<string, unknown> = { label, description, consequences };
    if ("dependencies" in entry) {
      const dependencies = normalizedString(entry.dependencies, 1, 4000);
      if (dependencies === null || looksLikeDocumentBytes(dependencies)) return null;
      option.dependencies = dependencies;
    }
    result.push(option);
  }
  return result;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, i) => key === wanted[i]);
}

function normalizedString(
  value: unknown,
  minimum: number,
  maximum: number,
): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  if (
    normalized.length < minimum ||
    normalized.length > maximum ||
    /[\0-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(normalized)
  ) {
    return null;
  }
  return normalized;
}

function looksLikeDocumentBytes(value: string): boolean {
  const compact = value.trim();
  if (
    /(?:data:[^;,]{1,100};base64,|%PDF-|JVBERi0|UEsDB|iVBORw0KGgo|\/9j\/)/i.test(
      compact,
    )
  ) {
    return true;
  }
  return /(?:^|\s)[A-Za-z0-9+/_-]{64,}={0,2}(?:$|\s|\.)/.test(compact);
}

function isUploadCompletion(method: string, segments: string[]): boolean {
  return (
    method === "POST" &&
    segments.length === 5 &&
    segments[0] === "api" &&
    segments[1] === "v1" &&
    segments[2] === "upload-intents" &&
    SAFE_ID.test(segments[3]) &&
    segments[4] === "complete"
  );
}

function allowlisted(method: string, segments: string[]): boolean {
  if (segments.some((segment) => !SAFE_ID.test(segment))) return false;
  const path = `/${segments.join("/")}`;
  return (
    (method === "GET" && path === "/api/v1/work-items") ||
    (method === "GET" && path === "/api/v1/cases") ||
    (method === "POST" && path === "/api/v1/cases") ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/upload-intents$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/upload-intents\/[A-Za-z0-9_-]+\/complete$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/tasks\/[A-Za-z0-9_-]+$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/documents\/[A-Za-z0-9_-]+\/review$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/documents\/[A-Za-z0-9_-]+\/confirmations$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/evidence$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conflicts$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/orchestration$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/orchestration\/advance$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/underwriting$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/legal$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/risk-review$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/risk-review\/disposition$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/risk-review\/challenges\/[A-Za-z0-9_-]+\/disposition$/.test(
        path,
      )) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/credit-ops$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/credit-ops\/actions\/[A-Za-z0-9_-]+\/authorize$/.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/credit-ops\/document-requests\/[A-Za-z0-9_-]+\/approve$/.test(
        path,
      )) ||
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/gap-request-batches$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/gap-request-batches$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/gap-request-batches\/[A-Za-z0-9_-]+\/disposition$/.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/intake-completion$/.test(path)) ||
    // Stage 7 — credit notification draft / approval / mock delivery.
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/notifications$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/notifications$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/notifications\/approve$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/notifications\/deliver$/.test(path)) ||
    // Stage 8 — contract package draft / redlines / signing gates.
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages\/redlines$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages\/approve$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages\/signature-authority$/.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/contract-packages\/sign$/.test(path)) ||
    // Stage 9 — per-asset security-perfection ledger + confirm gate.
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests\/confirm$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests\/items\/[A-Za-z0-9_-]+\/transition$/.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/security-interests\/[A-Za-z0-9_-]+\/items$/.test(
        path,
      )) ||
    // Stage 10 — disbursement condition ledger + confirm gate.
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conditions$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conditions$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conditions\/confirm$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conditions\/[A-Za-z0-9_-]+\/transition$/.test(
        path,
      )) ||
    // Stage 11 — proposed disbursement: list, create, the two human gates,
    // labelled-mock execute, and human reconciliation.
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements\/[A-Za-z0-9_-]+\/validate$/.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements\/[A-Za-z0-9_-]+\/authorize$/.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements\/[A-Za-z0-9_-]+\/execute$/.test(
        path,
      )) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/proposed-disbursements\/[A-Za-z0-9_-]+\/reconcile$/.test(
        path,
      )) ||
    // Stage 12 — post-credit monitoring: obligations, observations, covenants +
    // their tests, and the human alert disposition.
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/obligations$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/obligations$/.test(path)) ||
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/observations$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/observations$/.test(path)) ||
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/covenants$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/covenants$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/covenants\/[A-Za-z0-9_-]+\/test$/.test(
        path,
      )) ||
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/covenant-tests$/.test(path)) ||
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/alerts$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/monitoring\/alerts\/[A-Za-z0-9_-]+\/disposition$/.test(
        path,
      )) ||
    // Stage 13 — repayment ledger: open facility, append event, recomputed
    // ledger read (optional asOf), and a human collection note.
    (method === "POST" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/repayments$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/repayments\/[A-Za-z0-9_-]+\/events$/.test(path)) ||
    (method === "GET" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/repayments\/[A-Za-z0-9_-]+\/ledger$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/repayments\/[A-Za-z0-9_-]+\/notes$/.test(path)) ||
    // Stage 14 — settlement (14A) + recovery preparation (14B).
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/settlement\/check$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/settlement\/confirm$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/settlement$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/recovery$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/recovery$/.test(path)) ||
    (method === "POST" &&
      /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/recovery\/[A-Za-z0-9_-]+\/approve-strategy$/.test(
        path,
      )) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/handoffs$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/audit-events$/.test(path))
  );
}

function validCsrf(request: Request): boolean {
  const requestOrigin = request.headers.get("origin");
  const expectedOrigin = new URL(request.url).origin;
  const fetchSite = request.headers.get("sec-fetch-site");
  if (requestOrigin !== expectedOrigin || (fetchSite && fetchSite !== "same-origin")) {
    return false;
  }
  const cookieToken = readCookie(request.headers.get("cookie"), CSRF_COOKIE_NAME);
  const headerToken = request.headers.get(CSRF_HEADER_NAME);
  return (
    validOpaqueHeader(cookieToken) &&
    validOpaqueHeader(headerToken) &&
    cookieToken === headerToken
  );
}

function readCookie(header: string | null, name: string): string | null {
  for (const part of (header ?? "").split(";")) {
    const index = part.indexOf("=");
    if (index < 0 || part.slice(0, index).trim() !== name) continue;
    const rawValue = part.slice(index + 1).trim();
    try {
      return decodeURIComponent(rawValue);
    } catch {
      return null;
    }
  }
  return null;
}

function validCredential(value: string | null): value is string {
  return validOpaqueHeader(value) && value.length <= 8192;
}

function validOpaqueHeader(value: string | null): value is string {
  return typeof value === "string" && value.length > 0 && !/[\r\n\0]/.test(value);
}

function parseUpstreamBase(value: string | undefined): URL | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    url.pathname = `${url.pathname.replace(/\/$/, "")}/`;
    url.search = "";
    url.hash = "";
    return url;
  } catch {
    return null;
  }
}

function jsonError(status: number, code: string): Response {
  return Response.json(
    {
      code,
      messageVi: "Không thể hoàn tất yêu cầu.",
      correlationId: null,
      retryable: status >= 500,
    },
    { status, headers: { "cache-control": "no-store" } },
  );
}
