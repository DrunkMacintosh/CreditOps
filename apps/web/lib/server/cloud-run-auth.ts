const STS_TOKEN_URL = "https://sts.googleapis.com/v1/token";
const IAM_CREDENTIALS_URL = "https://iamcredentials.googleapis.com/v1";
const MAX_TOKEN_BYTES = 16 * 1024;
const CACHE_SKEW_MS = 60_000;
const SAFE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const PROJECT_NUMBER = /^[0-9]{1,20}$/;
const SERVICE_ACCOUNT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,254}@[A-Za-z0-9._-]+$/;
const JWT = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export class CloudRunAuthError extends Error {
  constructor(public readonly code: string) {
    super(code);
    this.name = "CloudRunAuthError";
  }
}

export interface CloudRunAuthOptions {
  audience?: string;
  projectNumber?: string;
  poolId?: string;
  providerId?: string;
  serviceAccountEmail?: string;
  fetcher?: typeof fetch;
  subjectToken?: string;
}

interface CachedToken {
  token: string;
  expiresAt: number;
}

const tokenCache = new Map<string, CachedToken>();

export function clearCloudRunAuthCache(): void {
  tokenCache.clear();
}

export async function getCloudRunServerlessAuthorization(
  request: Request,
  options: CloudRunAuthOptions = {},
): Promise<string> {
  const config = readConfig(options);
  const cached = tokenCache.get(config.audience);
  if (cached && cached.expiresAt - Date.now() > CACHE_SKEW_MS) {
    return cached.token;
  }

  const subjectToken = options.subjectToken ?? readVercelSubjectToken(request);
  if (!subjectToken) throw new CloudRunAuthError("VERCEL_OIDC_TOKEN_MISSING");
  if (!JWT.test(subjectToken) || subjectToken.length > MAX_TOKEN_BYTES) {
    throw new CloudRunAuthError("VERCEL_OIDC_TOKEN_INVALID");
  }

  const fetcher = options.fetcher ?? fetch;
  const stsToken = await exchangeSubjectToken(fetcher, config, subjectToken);
  const idToken = await generateIdToken(fetcher, config, stsToken);
  const expiresAt = jwtExpiry(idToken);
  if (expiresAt !== null) tokenCache.set(config.audience, { token: idToken, expiresAt });
  return idToken;
}

interface CloudRunConfig {
  audience: string;
  projectNumber: string;
  poolId: string;
  providerId: string;
  serviceAccountEmail: string;
}

function readConfig(options: CloudRunAuthOptions): CloudRunConfig {
  const audience = options.audience ?? process.env.CREDITOPS_API_AUDIENCE;
  const projectNumber = options.projectNumber ?? process.env.GCP_PROJECT_NUMBER;
  const poolId = options.poolId ?? process.env.GCP_WORKLOAD_IDENTITY_POOL_ID;
  const providerId =
    options.providerId ?? process.env.GCP_WORKLOAD_IDENTITY_POOL_PROVIDER_ID;
  const serviceAccountEmail =
    options.serviceAccountEmail ?? process.env.GCP_SERVICE_ACCOUNT_EMAIL;

  if (!audience || !isHttpsUrl(audience)) {
    throw new CloudRunAuthError("CLOUD_RUN_AUDIENCE_NOT_CONFIGURED");
  }
  if (!projectNumber || !PROJECT_NUMBER.test(projectNumber)) {
    throw new CloudRunAuthError("GCP_PROJECT_NUMBER_NOT_CONFIGURED");
  }
  if (!poolId || !SAFE_ID.test(poolId) || !providerId || !SAFE_ID.test(providerId)) {
    throw new CloudRunAuthError("GCP_WORKLOAD_IDENTITY_NOT_CONFIGURED");
  }
  if (!serviceAccountEmail || !SERVICE_ACCOUNT.test(serviceAccountEmail)) {
    throw new CloudRunAuthError("GCP_SERVICE_ACCOUNT_NOT_CONFIGURED");
  }
  return { audience, projectNumber, poolId, providerId, serviceAccountEmail };
}

function readVercelSubjectToken(request: Request): string | null {
  if (process.env.VERCEL === "1") {
    return request.headers.get("x-vercel-oidc-token");
  }
  return process.env.VERCEL_OIDC_TOKEN ?? null;
}

function identityProviderAudience(config: CloudRunConfig): string {
  return `//iam.googleapis.com/projects/${config.projectNumber}/locations/global/workloadIdentityPools/${config.poolId}/providers/${config.providerId}`;
}

async function exchangeSubjectToken(
  fetcher: typeof fetch,
  config: CloudRunConfig,
  subjectToken: string,
): Promise<string> {
  const body = new URLSearchParams({
    grant_type: "urn:ietf:params:oauth:grant-type:token-exchange",
    audience: identityProviderAudience(config),
    scope: "https://www.googleapis.com/auth/cloud-platform",
    requested_token_type: "urn:ietf:params:oauth:token-type:access_token",
    subject_token_type: "urn:ietf:params:oauth:token-type:jwt",
    subject_token: subjectToken,
  });
  let response: Response;
  try {
    response = await fetcher(STS_TOKEN_URL, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
      cache: "no-store",
    });
  } catch {
    throw new CloudRunAuthError("STS_EXCHANGE_FAILED");
  }
  if (!response.ok) throw new CloudRunAuthError("STS_EXCHANGE_FAILED");
  const payload = await readJsonBounded(response);
  const accessToken = payload?.access_token;
  if (typeof accessToken !== "string" || !accessToken || accessToken.length > MAX_TOKEN_BYTES) {
    throw new CloudRunAuthError("STS_TOKEN_INVALID");
  }
  return accessToken;
}

async function generateIdToken(
  fetcher: typeof fetch,
  config: CloudRunConfig,
  accessToken: string,
): Promise<string> {
  const account = encodeURIComponent(config.serviceAccountEmail);
  let response: Response;
  try {
    response = await fetcher(
      `${IAM_CREDENTIALS_URL}/projects/-/serviceAccounts/${account}:generateIdToken`,
      {
        method: "POST",
        headers: {
          accept: "application/json",
          authorization: `Bearer ${accessToken}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ audience: config.audience, includeEmail: true }),
        cache: "no-store",
      },
    );
  } catch {
    throw new CloudRunAuthError("ID_TOKEN_GENERATION_FAILED");
  }
  if (!response.ok) throw new CloudRunAuthError("ID_TOKEN_GENERATION_FAILED");
  const payload = await readJsonBounded(response);
  const token = payload?.token;
  if (typeof token !== "string" || !JWT.test(token) || token.length > MAX_TOKEN_BYTES) {
    throw new CloudRunAuthError("ID_TOKEN_INVALID");
  }
  return token;
}

async function readJsonBounded(response: Response): Promise<Record<string, unknown> | null> {
  const declared = response.headers.get("content-length");
  if (declared && /^\d+$/.test(declared) && Number(declared) > MAX_TOKEN_BYTES) return null;
  try {
    const bytes = await readBodyBounded(response.body);
    if (bytes === null) return null;
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    const value: unknown = JSON.parse(text);
    return isRecord(value) ? value : null;
  } catch {
    return null;
  }
}

async function readBodyBounded(
  stream: ReadableStream<Uint8Array<ArrayBufferLike>> | null,
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
      if (total > MAX_TOKEN_BYTES) {
        await reader.cancel("token-response-size-limit");
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

function jwtExpiry(token: string): number | null {
  try {
    const encoded = token.split(".")[1];
    const payload = JSON.parse(base64UrlDecode(encoded)) as { exp?: unknown };
    return typeof payload.exp === "number" && Number.isFinite(payload.exp)
      ? payload.exp * 1000
      : null;
  } catch {
    return null;
  }
}

function base64UrlDecode(value: string): string {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return atob(padded);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isHttpsUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (
      parsed.protocol === "https:" &&
      !parsed.username &&
      !parsed.password &&
      (parsed.pathname === "" || parsed.pathname === "/") &&
      !parsed.search &&
      !parsed.hash
    );
  } catch {
    return false;
  }
}
