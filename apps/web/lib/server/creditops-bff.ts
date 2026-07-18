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
  const entries = [...parameters.entries()];
  const isCaseList =
    method === "GET" &&
    segments.length === 3 &&
    segments.join("/") === "api/v1/cases";
  if (!isCaseList) return entries.length === 0 ? "" : null;

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

function validLimit(value: string): boolean {
  return /^\d{1,3}$/.test(value) && Number(value) >= 1 && Number(value) <= 100;
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

  if (isConfirmationSubmission(segments)) {
    return canonicalizeConfirmation(value);
  }

  return null;
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
    (method === "GET" && path === "/api/v1/cases") ||
    (method === "POST" && path === "/api/v1/cases") ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/upload-intents$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/upload-intents\/[A-Za-z0-9_-]+\/complete$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/tasks\/[A-Za-z0-9_-]+$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/documents\/[A-Za-z0-9_-]+\/review$/.test(path)) ||
    (method === "POST" && /^\/api\/v1\/documents\/[A-Za-z0-9_-]+\/confirmations$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/evidence$/.test(path)) ||
    (method === "GET" && /^\/api\/v1\/cases\/[A-Za-z0-9_-]+\/conflicts$/.test(path))
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
