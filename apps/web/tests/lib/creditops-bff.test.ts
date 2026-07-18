import { describe, expect, it, vi } from "vitest";

import { CreditOpsApiClient } from "../../lib/api/client";
import {
  CSRF_COOKIE_NAME,
  CSRF_HEADER_NAME,
  SESSION_COOKIE_NAME,
  proxyCreditOpsRequest as proxyCreditOpsRequestImpl,
} from "../../lib/server/creditops-bff";

const upstreamBaseUrl = "https://creditops-api.invalid";

function proxyCreditOpsRequest(
  request: Request,
  pathSegments: string[],
  dependencies: Parameters<typeof proxyCreditOpsRequestImpl>[2] = {},
) {
  return proxyCreditOpsRequestImpl(request, pathSegments, {
    serverlessAuthorization: async () => "test-cloud-run-id-token",
    ...dependencies,
  });
}

function request(
  path: string,
  init: RequestInit = {},
  cookies = `${SESSION_COOKIE_NAME}=workforce-token`,
) {
  const headers = new Headers(init.headers);
  if (cookies) headers.set("cookie", cookies);
  return new Request(`https://app.invalid/api/creditops${path}`, {
    ...init,
    headers,
  });
}

describe("CreditOps JSON BFF", () => {
  it("fails closed when Cloud Run IAM authorization is not configured", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequestImpl(
      request("/api/v1/cases"),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(503);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("reads request and response bodies through bounded streams without whole-body helpers", async () => {
    const upstream = new Response(JSON.stringify({ id: "case-1" }), {
      status: 201,
      headers: { "content-type": "application/json" },
    });
    const responseArrayBuffer = vi
      .spyOn(upstream, "arrayBuffer")
      .mockRejectedValue(new Error("whole response body read"));
    const fetcher = vi.fn().mockResolvedValue(upstream);
    const incoming = request(
      "/api/v1/cases",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          origin: "https://app.invalid",
          [CSRF_HEADER_NAME]: "csrf-value",
        },
        body: JSON.stringify({ requestedAmount: "1", purpose: "Vốn lưu động" }),
      },
      `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
    );
    const requestText = vi
      .spyOn(incoming, "text")
      .mockRejectedValue(new Error("whole request body read"));

    const response = await proxyCreditOpsRequest(
      incoming,
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(201);
    expect(requestText).not.toHaveBeenCalled();
    expect(responseArrayBuffer).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("stops an undeclared oversized request stream before calling upstream", async () => {
    const fetcher = vi.fn();
    const oversized = "x".repeat(1024 * 1024 + 1);
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/cases",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            origin: "https://app.invalid",
            [CSRF_HEADER_NAME]: "csrf-value",
          },
          body: oversized,
        },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(413);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("cancels an oversized upstream stream instead of buffering the full response", async () => {
    const cancel = vi.fn();
    let pullCount = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        pullCount += 1;
        controller.enqueue(new Uint8Array(512 * 1024));
        if (pullCount === 10) controller.close();
      },
      cancel,
    });
    const fetcher = vi.fn().mockResolvedValue(
      new Response(body, {
        headers: { "content-type": "application/json" },
      }),
    );

    const response = await proxyCreditOpsRequest(
      request("/api/v1/cases"),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(502);
    expect(cancel).toHaveBeenCalledOnce();
    expect(pullCount).toBeLessThanOrEqual(6);
  });

  it("forwards an allowlisted request with server-side bearer auth and strips sensitive response headers", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: {
          "content-type": "application/json",
          "set-cookie": "leaked=secret",
          authorization: "Bearer leaked",
          server: "private-upstream",
          "x-request-id": "request-1",
          "x-correlation-id": "correlation-1",
        },
      }),
    );

    const response = await proxyCreditOpsRequest(
      request("/api/v1/cases"),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("https://creditops-api.invalid/api/v1/cases");
    expect(new Headers(init.headers).get("authorization")).toBe(
      "Bearer workforce-token",
    );
    expect(new Headers(init.headers).get("x-serverless-authorization")).toBe(
      "Bearer test-cloud-run-id-token",
    );
    expect(new Headers(init.headers).has("cookie")).toBe(false);
    expect(response.headers.get("x-request-id")).toBe("request-1");
    expect(response.headers.get("x-correlation-id")).toBe("correlation-1");
    expect(response.headers.has("set-cookie")).toBe(false);
    expect(response.headers.has("authorization")).toBe(false);
    expect(response.headers.has("server")).toBe(false);
  });

  it("reconstructs only the documented case-list query parameters", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      Response.json({ items: [], nextCursor: null }),
    );
    const cursor = "123e4567-e89b-12d3-a456-426614174000";
    const response = await proxyCreditOpsRequest(
      request(`/api/v1/cases?limit=20&cursor=${cursor}`),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0]).toBe(
      `https://creditops-api.invalid/api/v1/cases?cursor=${cursor}&limit=20`,
    );
  });

  it.each([
    "/api/v1/cases?documentBytes=JVBERi0%3D",
    "/api/v1/cases?limit=20&limit=21",
    "/api/v1/cases?limit=0",
    "/api/v1/cases?cursor=not-a-uuid",
  ])("rejects an undocumented or invalid case-list query: %s", async (path) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request(path),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    ["/api/v1/cases/case-1?debug=true", ["api", "v1", "cases", "case-1"]],
    ["/api/v1/tasks/task-1?payload=JVBERi0%3D", ["api", "v1", "tasks", "task-1"]],
  ])("rejects query parameters on an exact resource route: %s", async (path, segments) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(request(path), segments, {
      fetcher,
      upstreamBaseUrl,
    });

    expect(response.status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(["text/application/json", "application/json-patch+json"])(
    "rejects a misleading upstream media type %s",
    async (contentType) => {
      const fetcher = vi.fn().mockResolvedValue(
        new Response("{}", { headers: { "content-type": contentType } }),
      );
      const response = await proxyCreditOpsRequest(
        request("/api/v1/cases"),
        ["api", "v1", "cases"],
        { fetcher, upstreamBaseUrl },
      );

      expect(response.status).toBe(502);
    },
  );

  it("fails closed without a server-side session token", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request("/api/v1/cases", {}, ""),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(401);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    ["GET", "/api/v1/documents/document-1", ["api", "v1", "documents", "document-1"]],
    ["DELETE", "/api/v1/cases/case-1", ["api", "v1", "cases", "case-1"]],
  ])("rejects non-allowlisted %s paths", async (method, path, segments) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request(path, { method }),
      segments,
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(["multipart/form-data; boundary=secret", "application/octet-stream", "application/pdf"])(
    "rejects direct document body type %s",
    async (contentType) => {
      const fetcher = vi.fn();
      const cookies = `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`;
      const response = await proxyCreditOpsRequest(
        request(
          "/api/v1/cases",
          {
            method: "POST",
            headers: {
              "content-type": contentType,
              origin: "https://app.invalid",
              [CSRF_HEADER_NAME]: "csrf-value",
            },
            body: "document bytes",
          },
          cookies,
        ),
        ["api", "v1", "cases"],
        { fetcher, upstreamBaseUrl },
      );

      expect(response.status).toBe(415);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it.each([
    [undefined, "csrf-value"],
    ["https://evil.invalid", "csrf-value"],
    ["https://app.invalid", undefined],
    ["https://app.invalid", "different-value"],
  ])("fails closed for invalid mutation origin/token %#", async (origin, headerToken) => {
    const fetcher = vi.fn();
    const headers = new Headers({ "content-type": "application/json" });
    if (origin) headers.set("origin", origin);
    if (headerToken) headers.set(CSRF_HEADER_NAME, headerToken);
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/cases",
        { method: "POST", headers, body: "{}" },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards only JSON mutations with a matching double-submit token", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      Response.json({ id: "case-1" }, { status: 201 }),
    );
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/upload-intents/intent-1/complete",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            origin: "https://app.invalid",
            [CSRF_HEADER_NAME]: "csrf-value",
            "idempotency-key": "stable-key",
          },
          body: "{}",
        },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "upload-intents", "intent-1", "complete"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(201);
    const init = fetcher.mock.calls[0][1];
    const forwardedHeaders = new Headers(init.headers);
    expect(forwardedHeaders.get("idempotency-key")).toBe("stable-key");
    expect(forwardedHeaders.has(CSRF_HEADER_NAME)).toBe(false);
    expect(init.body).toBe("{}");
  });

  it("validates and reconstructs the exact create-case DTO", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      Response.json({ id: "case-1" }, { status: 201 }),
    );
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/cases",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            origin: "https://app.invalid",
            [CSRF_HEADER_NAME]: "csrf-value",
          },
          body: JSON.stringify({
            requestedAmount: " 5000000000 ",
            purpose: " Vốn lưu động ",
          }),
        },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({ requestedAmount: "5000000000", purpose: "Vốn lưu động" }),
    );
  });

  it.each([
    { requestedAmount: "1", purpose: "Vốn lưu động", documentBytes: "JVBERi0x" },
    { requestedAmount: "0", purpose: "Vốn lưu động" },
    { requestedAmount: "1", purpose: "A".repeat(501) },
    { requestedAmount: "1", purpose: `JVBERi0${"A".repeat(80)}` },
    {
      requestedAmount: "1",
      purpose: "Nội dung data:application/pdf;base64,JVBERi0xLjQ=",
    },
    { requestedAmount: "1", purpose: `${"A".repeat(70)}_-` },
  ])("rejects invalid or byte-carrying create-case JSON %#", async (body) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/cases",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            origin: "https://app.invalid",
            [CSRF_HEADER_NAME]: "csrf-value",
          },
          body: JSON.stringify(body),
        },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "cases"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("validates and reconstructs the exact upload-intent DTO", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ intentId: "intent-1" }));
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/cases/case-1/upload-intents",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            origin: "https://app.invalid",
            [CSRF_HEADER_NAME]: "csrf-value",
          },
          body: JSON.stringify({
            fileName: " tong-hop.pdf ",
            contentType: "application/pdf",
            sizeBytes: 42,
          }),
        },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "cases", "case-1", "upload-intents"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({
        fileName: "tong-hop.pdf",
        contentType: "application/pdf",
        sizeBytes: 42,
      }),
    );
  });

  it.each([
    { fileName: "scan.pdf", contentType: "application/pdf", sizeBytes: 1, data: "JVBERi0=" },
    { fileName: "../scan.pdf", contentType: "application/pdf", sizeBytes: 1 },
    { fileName: "scan.pdf", contentType: "application/pdf", sizeBytes: 0 },
    { fileName: `JVBERi0${"A".repeat(80)}.pdf`, contentType: "application/pdf", sizeBytes: 1 },
    { fileName: "scan.exe", contentType: "application/pdf", sizeBytes: 1 },
  ])("rejects invalid or byte-carrying upload-intent JSON %#", async (body) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/cases/case-1/upload-intents",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            origin: "https://app.invalid",
            [CSRF_HEADER_NAME]: "csrf-value",
          },
          body: JSON.stringify(body),
        },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "cases", "case-1", "upload-intents"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("requires upload completion to have the exact empty JSON object", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/upload-intents/intent-1/complete",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            origin: "https://app.invalid",
            [CSRF_HEADER_NAME]: "csrf-value",
            "idempotency-key": "stable-key",
          },
          body: JSON.stringify({ documentBytes: "JVBERi0=" }),
        },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "upload-intents", "intent-1", "complete"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("fails closed when upload completion has no idempotency key", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request(
        "/api/v1/upload-intents/intent-1/complete",
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            origin: "https://app.invalid",
            [CSRF_HEADER_NAME]: "csrf-value",
          },
          body: "{}",
        },
        `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`,
      ),
      ["api", "v1", "upload-intents", "intent-1", "complete"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

const CONFIRMATION_COOKIES = `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`;

function confirmationRequest(body: unknown) {
  return request(
    "/api/v1/documents/document-1/confirmations",
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        origin: "https://app.invalid",
        [CSRF_HEADER_NAME]: "csrf-value",
      },
      body: JSON.stringify(body),
    },
    CONFIRMATION_COOKIES,
  );
}

const confirmationSegments = ["api", "v1", "documents", "document-1", "confirmations"];

describe("CreditOps review-workspace routes", () => {
  it.each([
    ["/api/v1/documents/document-1/review", ["api", "v1", "documents", "document-1", "review"]],
    ["/api/v1/cases/case-1/evidence", ["api", "v1", "cases", "case-1", "evidence"]],
    ["/api/v1/cases/case-1/conflicts", ["api", "v1", "cases", "case-1", "conflicts"]],
  ])("forwards the allowlisted review GET %s", async (path, segments) => {
    const fetcher = vi.fn().mockResolvedValue(
      Response.json({ items: [] }, { status: 200 }),
    );
    const response = await proxyCreditOpsRequest(request(path), segments, {
      fetcher,
      upstreamBaseUrl,
    });

    expect(response.status).toBe(200);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toBe(`https://creditops-api.invalid${path}`);
  });

  it.each([
    ["/api/v1/documents/document-1/review?foo=bar", ["api", "v1", "documents", "document-1", "review"]],
    ["/api/v1/cases/case-1/evidence?cursor=x", ["api", "v1", "cases", "case-1", "evidence"]],
    ["/api/v1/cases/case-1/conflicts?limit=5", ["api", "v1", "cases", "case-1", "conflicts"]],
  ])("rejects query parameters on the review GET %s", async (path, segments) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(request(path), segments, {
      fetcher,
      upstreamBaseUrl,
    });

    expect(response.status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps an unlisted document sub-route closed", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request("/api/v1/documents/document-1/preview"),
      ["api", "v1", "documents", "document-1", "preview"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards a valid confirmation body canonically", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      confirmationRequest({
        expectedDocumentVersion: 3,
        dispositions: [
          { candidateId: "cand-1", disposition: "ACCEPTED" },
          {
            candidateId: "cand-2",
            disposition: "CORRECTED",
            correctedValue: "5000000000",
            rationale: "Điều chỉnh theo hợp đồng tổng hợp",
          },
          { candidateId: "cand-3", disposition: "ABSENT" },
        ],
      }),
      confirmationSegments,
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://creditops-api.invalid/api/v1/documents/document-1/confirmations",
    );
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({
        expectedDocumentVersion: 3,
        dispositions: [
          { candidateId: "cand-1", disposition: "ACCEPTED" },
          {
            candidateId: "cand-2",
            disposition: "CORRECTED",
            correctedValue: "5000000000",
            rationale: "Điều chỉnh theo hợp đồng tổng hợp",
          },
          { candidateId: "cand-3", disposition: "ABSENT" },
        ],
      }),
    );
  });

  it.each([
    {
      name: "missing rationale on a corrected disposition",
      body: {
        expectedDocumentVersion: 1,
        dispositions: [
          { candidateId: "cand-1", disposition: "CORRECTED", correctedValue: "5000000000" },
        ],
      },
    },
    {
      name: "extra top-level key",
      body: {
        expectedDocumentVersion: 1,
        dispositions: [{ candidateId: "cand-1", disposition: "ACCEPTED" }],
        extra: true,
      },
    },
    {
      name: "duplicate candidateId",
      body: {
        expectedDocumentVersion: 1,
        dispositions: [
          { candidateId: "cand-1", disposition: "ACCEPTED" },
          { candidateId: "cand-1", disposition: "ABSENT" },
        ],
      },
    },
    {
      name: "unknown disposition",
      body: {
        expectedDocumentVersion: 1,
        dispositions: [{ candidateId: "cand-1", disposition: "APPROVED" }],
      },
    },
    {
      name: "non-corrected disposition carrying a corrected value",
      body: {
        expectedDocumentVersion: 1,
        dispositions: [
          { candidateId: "cand-1", disposition: "ACCEPTED", correctedValue: "5000000000" },
        ],
      },
    },
    {
      name: "more than 200 dispositions",
      body: {
        expectedDocumentVersion: 1,
        dispositions: Array.from({ length: 201 }, (_unused, index) => ({
          candidateId: `cand-${index}`,
          disposition: "ACCEPTED",
        })),
      },
    },
    {
      name: "version below the accepted range",
      body: {
        expectedDocumentVersion: 0,
        dispositions: [{ candidateId: "cand-1", disposition: "ACCEPTED" }],
      },
    },
  ])("rejects an invalid confirmation body: $name", async ({ body }) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(confirmationRequest(body), confirmationSegments, {
      fetcher,
      upstreamBaseUrl,
    });

    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("browser CreditOps client", () => {
  it("uses the same-origin BFF and adds the non-secret CSRF token to mutations", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          items: [],
          nextCursor: null,
          capabilities: { canCreateCase: false },
        }),
      )
      .mockResolvedValueOnce(
        Response.json({
          id: "case-1",
          version: 1,
          assignedOfficerId: "officer-1",
          requestedAmount: "1",
          purpose: "Tổng hợp",
          capabilities: {
            canUpload: false,
            canConfirm: false,
            canCompleteIntake: false,
          },
        }),
      );
    const client = new CreditOpsApiClient(undefined, fetcher, () => "csrf-value");

    await client.listCases();
    await client.createCase({ requestedAmount: "1", purpose: "Tổng hợp" });

    expect(fetcher.mock.calls[0][0]).toBe("/api/creditops/api/v1/cases");
    expect(fetcher.mock.calls[1][0]).toBe("/api/creditops/api/v1/cases");
    expect(new Headers(fetcher.mock.calls[1][1].headers).get(CSRF_HEADER_NAME)).toBe(
      "csrf-value",
    );
  });
});

const GAP_COOKIES = `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`;
const ITEM_1 = "11111111-1111-4111-8111-111111111111";
const ITEM_2 = "22222222-2222-4222-8222-222222222222";

function jsonMutation(path: string, body: unknown) {
  return request(
    path,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        origin: "https://app.invalid",
        [CSRF_HEADER_NAME]: "csrf-value",
      },
      body: JSON.stringify(body),
    },
    GAP_COOKIES,
  );
}

describe("CreditOps gap-request / intake / handoff / audit routes", () => {
  it("forwards the allowlisted gap-request batch GET", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ batch: {} }, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/gap-request-batches"),
      ["api", "v1", "cases", "case-1", "gap-request-batches"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://creditops-api.invalid/api/v1/cases/case-1/gap-request-batches",
    );
  });

  it("rejects query parameters on the gap-request batch GET", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/gap-request-batches?cursor=x"),
      ["api", "v1", "cases", "case-1", "gap-request-batches"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards the assemble-or-get POST with an exactly-empty body", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ batchId: "b1" }, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      jsonMutation("/api/v1/cases/case-1/gap-request-batches", {}),
      ["api", "v1", "cases", "case-1", "gap-request-batches"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe("{}");
  });

  it("rejects a non-empty assemble-or-get POST body", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      jsonMutation("/api/v1/cases/case-1/gap-request-batches", { extra: true }),
      ["api", "v1", "cases", "case-1", "gap-request-batches"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("validates and reconstructs a REJECTED disposition (no maps)", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      jsonMutation(`/api/v1/cases/case-1/gap-request-batches/batch-1/disposition`, {
        dispositionType: "REJECTED",
        rationale: "  Không phù hợp, từ chối đợt yêu cầu.  ",
      }),
      ["api", "v1", "cases", "case-1", "gap-request-batches", "batch-1", "disposition"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({
        dispositionType: "REJECTED",
        rationale: "Không phù hợp, từ chối đợt yêu cầu.",
      }),
    );
  });

  it("validates and reconstructs an APPROVED_WITH_CHANGES disposition with nested maps", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      jsonMutation(`/api/v1/cases/case-1/gap-request-batches/batch-1/disposition`, {
        dispositionType: "APPROVED_WITH_CHANGES",
        rationale: "Giữ một mục, chỉnh một mục.",
        itemDispositions: { [ITEM_1]: "APPROVED", [ITEM_2]: "EDITED" },
        editedTexts: { [ITEM_2]: "  Nội dung yêu cầu đã chỉnh sửa.  " },
      }),
      ["api", "v1", "cases", "case-1", "gap-request-batches", "batch-1", "disposition"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({
        dispositionType: "APPROVED_WITH_CHANGES",
        rationale: "Giữ một mục, chỉnh một mục.",
        itemDispositions: { [ITEM_1]: "APPROVED", [ITEM_2]: "EDITED" },
        editedTexts: { [ITEM_2]: "Nội dung yêu cầu đã chỉnh sửa." },
      }),
    );
  });

  it.each([
    {
      name: "unknown disposition type",
      body: { dispositionType: "APPROVED_MAYBE", rationale: "x" },
    },
    {
      name: "missing rationale",
      body: { dispositionType: "APPROVED_ALL" },
    },
    {
      name: "extra top-level key",
      body: { dispositionType: "APPROVED_ALL", rationale: "x", note: "y" },
    },
    {
      name: "non-uuid item key",
      body: {
        dispositionType: "APPROVED_WITH_CHANGES",
        rationale: "x",
        itemDispositions: { "not-a-uuid": "APPROVED" },
      },
    },
    {
      name: "unknown item disposition value",
      body: {
        dispositionType: "APPROVED_WITH_CHANGES",
        rationale: "x",
        itemDispositions: { [ITEM_1]: "MAYBE" },
      },
    },
    {
      name: "non-record itemDispositions",
      body: {
        dispositionType: "APPROVED_WITH_CHANGES",
        rationale: "x",
        itemDispositions: [ITEM_1],
      },
    },
    {
      name: "non-uuid edited-text key",
      body: {
        dispositionType: "APPROVED_WITH_CHANGES",
        rationale: "x",
        itemDispositions: { [ITEM_1]: "EDITED" },
        editedTexts: { "not-a-uuid": "text" },
      },
    },
    {
      name: "document-bytes rationale",
      body: { dispositionType: "REJECTED", rationale: `JVBERi0${"A".repeat(80)}` },
    },
  ])("rejects an invalid gap disposition body: $name", async ({ body }) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      jsonMutation(`/api/v1/cases/case-1/gap-request-batches/batch-1/disposition`, body),
      ["api", "v1", "cases", "case-1", "gap-request-batches", "batch-1", "disposition"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards the intake-completion POST with an exactly-empty body", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ handoffId: "h1" }, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      jsonMutation("/api/v1/cases/case-1/intake-completion", {}),
      ["api", "v1", "cases", "case-1", "intake-completion"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe("{}");
  });

  it("rejects a non-empty intake-completion POST body", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      jsonMutation("/api/v1/cases/case-1/intake-completion", { force: true }),
      ["api", "v1", "cases", "case-1", "intake-completion"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards the handoffs GET and rejects a query on it", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ handoffId: "h1" }, { status: 200 }));
    const ok = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/handoffs"),
      ["api", "v1", "cases", "case-1", "handoffs"],
      { fetcher, upstreamBaseUrl },
    );
    expect(ok.status).toBe(200);

    const rejected = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/handoffs?limit=5"),
      ["api", "v1", "cases", "case-1", "handoffs"],
      { fetcher, upstreamBaseUrl },
    );
    expect(rejected.status).toBe(400);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("forwards the audit-events GET and reconstructs its cursor/limit query", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      Response.json({ events: [], nextCursor: null }, { status: 200 }),
    );
    const cursor = "123e4567-e89b-12d3-a456-426614174000";
    const response = await proxyCreditOpsRequest(
      request(`/api/v1/cases/case-1/audit-events?limit=25&cursor=${cursor}`),
      ["api", "v1", "cases", "case-1", "audit-events"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0]).toBe(
      `https://creditops-api.invalid/api/v1/cases/case-1/audit-events?cursor=${cursor}&limit=25`,
    );
  });

  it.each([
    "/api/v1/cases/case-1/audit-events?cursor=not-a-uuid",
    "/api/v1/cases/case-1/audit-events?limit=0",
    "/api/v1/cases/case-1/audit-events?foo=bar",
  ])("rejects an invalid audit-events query: %s", async (path) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request(path),
      ["api", "v1", "cases", "case-1", "audit-events"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps the audit-events POST closed (read-only route)", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      jsonMutation("/api/v1/cases/case-1/audit-events", {}),
      ["api", "v1", "cases", "case-1", "audit-events"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("CreditOps work-queue route", () => {
  it("forwards the allowlisted work-items GET with no query", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [] }, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      request("/api/v1/work-items"),
      ["api", "v1", "work-items"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0]).toBe("https://creditops-api.invalid/api/v1/work-items");
  });

  it("reconstructs the documented work-items limit query", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [] }, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      request("/api/v1/work-items?limit=25"),
      ["api", "v1", "work-items"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://creditops-api.invalid/api/v1/work-items?limit=25",
    );
  });

  it("accepts the backend's upper limit bound of 200", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [] }, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      request("/api/v1/work-items?limit=200"),
      ["api", "v1", "work-items"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://creditops-api.invalid/api/v1/work-items?limit=200",
    );
  });

  it.each([
    "/api/v1/work-items?limit=0",
    "/api/v1/work-items?limit=201",
    "/api/v1/work-items?limit=20&limit=21",
    "/api/v1/work-items?cursor=123e4567-e89b-12d3-a456-426614174000",
    "/api/v1/work-items?foo=bar",
    "/api/v1/work-items?documentBytes=JVBERi0%3D",
  ])("rejects an undocumented or invalid work-items query: %s", async (path) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      request(path),
      ["api", "v1", "work-items"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps the work-items POST closed (read-only route)", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      jsonMutation("/api/v1/work-items", {}),
      ["api", "v1", "work-items"],
      { fetcher, upstreamBaseUrl },
    );

    expect(response.status).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

// --- Stage 7-10 gate surfaces -------------------------------------------------

const STAGE_COOKIES = `${SESSION_COOKIE_NAME}=workforce-token; ${CSRF_COOKIE_NAME}=csrf-value`;
const DRAFT_UUID_UPPER = "AAAAAAAA-1111-4111-8111-111111111111";
const DRAFT_UUID_LOWER = "aaaaaaaa-1111-4111-8111-111111111111";

function stageMutation(path: string, body: unknown) {
  return request(
    path,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        origin: "https://app.invalid",
        [CSRF_HEADER_NAME]: "csrf-value",
      },
      body: JSON.stringify(body),
    },
    STAGE_COOKIES,
  );
}

function seg(path: string): string[] {
  return path.replace(/^\//, "").split("/");
}

describe("CreditOps stage-7 notification routes", () => {
  it("forwards the notification GET and rejects a query on it", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ draft: null }, { status: 200 }));
    const ok = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/notifications"),
      seg("/api/v1/cases/case-1/notifications"),
      { fetcher, upstreamBaseUrl },
    );
    expect(ok.status).toBe(200);

    const rejected = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/notifications?cursor=x"),
      seg("/api/v1/cases/case-1/notifications"),
      { fetcher, upstreamBaseUrl },
    );
    expect(rejected.status).toBe(400);
  });

  it("forwards the create-draft POST with an exactly-empty body", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ id: "d1" }, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/notifications", {}),
      seg("/api/v1/cases/case-1/notifications"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe("{}");
  });

  it("rejects a non-empty create-draft POST body", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/notifications", { force: true }),
      seg("/api/v1/cases/case-1/notifications"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("validates and lower-cases the approve body pinned to the draft id", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/notifications/approve", {
        draftId: DRAFT_UUID_UPPER,
        rationale: "  Đủ căn cứ duyệt nội dung thông báo.  ",
      }),
      seg("/api/v1/cases/case-1/notifications/approve"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({
        draftId: DRAFT_UUID_LOWER,
        rationale: "Đủ căn cứ duyệt nội dung thông báo.",
      }),
    );
  });

  it.each([
    { name: "missing draftId", body: { rationale: "x" } },
    { name: "non-uuid draftId", body: { draftId: "not-a-uuid", rationale: "x" } },
    { name: "extra key", body: { draftId: DRAFT_UUID_LOWER, rationale: "x", note: "y" } },
    {
      name: "document-bytes rationale",
      body: { draftId: DRAFT_UUID_LOWER, rationale: `JVBERi0${"A".repeat(80)}` },
    },
  ])("rejects an invalid approve body: $name", async ({ body }) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/notifications/approve", body),
      seg("/api/v1/cases/case-1/notifications/approve"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards the deliver POST with an optional note (and an empty body)", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ id: "r1" }, { status: 201 }));
    const withNote = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/notifications/deliver", {
        receiptNote: "  Đã giao qua kênh mock.  ",
      }),
      seg("/api/v1/cases/case-1/notifications/deliver"),
      { fetcher, upstreamBaseUrl },
    );
    expect(withNote.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({ receiptNote: "Đã giao qua kênh mock." }),
    );

    const empty = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/notifications/deliver", {}),
      seg("/api/v1/cases/case-1/notifications/deliver"),
      { fetcher, upstreamBaseUrl },
    );
    expect(empty.status).toBe(201);
    expect(fetcher.mock.calls[1][1].body).toBe("{}");
  });

  it("rejects a deliver body with an undeclared key", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/notifications/deliver", { channel: "email" }),
      seg("/api/v1/cases/case-1/notifications/deliver"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("CreditOps stage-8 contract-package routes", () => {
  it("forwards the contract-packages GET and the empty create POST", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ package: {} }, { status: 200 }));
    const get = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/contract-packages"),
      seg("/api/v1/cases/case-1/contract-packages"),
      { fetcher, upstreamBaseUrl },
    );
    expect(get.status).toBe(200);

    const create = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/contract-packages", {}),
      seg("/api/v1/cases/case-1/contract-packages"),
      { fetcher, upstreamBaseUrl },
    );
    expect(create.status).toBe(200);
    expect(fetcher.mock.calls[1][1].body).toBe("{}");
  });

  it("validates and reconstructs a redline body", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/contract-packages/redlines", {
        changeNote: "  Sửa điều khoản lãi suất.  ",
        changedContent: "  Nội dung hợp đồng đã chỉnh sửa.  ",
      }),
      seg("/api/v1/cases/case-1/contract-packages/redlines"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({
        changeNote: "Sửa điều khoản lãi suất.",
        changedContent: "Nội dung hợp đồng đã chỉnh sửa.",
      }),
    );
  });

  it.each([
    { name: "missing changedContent", body: { changeNote: "x" } },
    { name: "extra key", body: { changeNote: "x", changedContent: "y", who: "z" } },
  ])("rejects an invalid redline body: $name", async ({ body }) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/contract-packages/redlines", body),
      seg("/api/v1/cases/case-1/contract-packages/redlines"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    "/api/v1/cases/case-1/contract-packages/approve",
    "/api/v1/cases/case-1/contract-packages/signature-authority",
  ])("reconstructs the {rationale} body for %s", async (path) => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      stageMutation(path, { rationale: "  Đủ điều kiện.  " }),
      seg(path),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][1].body).toBe(JSON.stringify({ rationale: "Đủ điều kiện." }));
  });

  it("rejects an approve body with a missing rationale", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/contract-packages/approve", { note: "x" }),
      seg("/api/v1/cases/case-1/contract-packages/approve"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("reconstructs a sign body with signer names and an optional note", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/contract-packages/sign", {
        signerNames: [" Nguyễn Văn A ", "Trần B"],
        evidenceNote: " Ký mô phỏng. ",
      }),
      seg("/api/v1/cases/case-1/contract-packages/sign"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({ signerNames: ["Nguyễn Văn A", "Trần B"], evidenceNote: "Ký mô phỏng." }),
    );
  });

  it.each([
    { name: "missing signerNames", body: { evidenceNote: "x" } },
    { name: "empty signerNames", body: { signerNames: [] } },
    { name: "non-string signer", body: { signerNames: [1, 2] } },
  ])("rejects an invalid sign body: $name", async ({ body }) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/contract-packages/sign", body),
      seg("/api/v1/cases/case-1/contract-packages/sign"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("CreditOps stage-9 security-interest routes", () => {
  it("forwards the ledger GET", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ interests: [] }, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/security-interests"),
      seg("/api/v1/cases/case-1/security-interests"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(200);
  });

  it("reconstructs a create-interest body with a closed asset kind", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/security-interests", {
        assetKind: "REAL_ESTATE",
        assetDescription: "  Nhà đất số 10.  ",
        ownerName: " Ông A ",
      }),
      seg("/api/v1/cases/case-1/security-interests"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({
        assetDescription: "Nhà đất số 10.",
        assetKind: "REAL_ESTATE",
        ownerName: "Ông A",
      }),
    );
  });

  it.each([
    { name: "unknown asset kind", body: { assetKind: "GOLD", assetDescription: "x" } },
    { name: "extra key", body: { assetKind: "OTHER", assetDescription: "x", rank: 1 } },
  ])("rejects an invalid create-interest body: $name", async ({ body }) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/security-interests", body),
      seg("/api/v1/cases/case-1/security-interests"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("reconstructs an add-item body with evidence refs and a valid date", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/security-interests/interest-1/items", {
        requirement: "  Đăng ký thế chấp.  ",
        evidenceRefs: [" ref-1 "],
        effectiveDate: "2026-07-18",
      }),
      seg("/api/v1/cases/case-1/security-interests/interest-1/items"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({
        requirement: "Đăng ký thế chấp.",
        evidenceRefs: ["ref-1"],
        effectiveDate: "2026-07-18",
      }),
    );
  });

  it("rejects an add-item body with an impossible date", async () => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/security-interests/interest-1/items", {
        requirement: "x",
        effectiveDate: "2026-13-40",
      }),
      seg("/api/v1/cases/case-1/security-interests/interest-1/items"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("reconstructs an item transition and rejects an unknown status", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 200 }));
    const ok = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/security-interests/items/item-1/transition", {
        toStatus: "COMPLETED",
        evidenceRefs: ["ref-1"],
      }),
      seg("/api/v1/cases/case-1/security-interests/items/item-1/transition"),
      { fetcher, upstreamBaseUrl },
    );
    expect(ok.status).toBe(200);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({ toStatus: "COMPLETED", evidenceRefs: ["ref-1"] }),
    );

    const rejected = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/security-interests/items/item-1/transition", {
        toStatus: "DONE",
      }),
      seg("/api/v1/cases/case-1/security-interests/items/item-1/transition"),
      { fetcher, upstreamBaseUrl },
    );
    expect(rejected.status).toBe(422);
  });

  it("reconstructs the {rationale} security confirm body", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/security-interests/confirm", {
        rationale: " Đã hoàn thiện. ",
      }),
      seg("/api/v1/cases/case-1/security-interests/confirm"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][1].body).toBe(JSON.stringify({ rationale: "Đã hoàn thiện." }));
  });
});

describe("CreditOps stage-10 condition routes", () => {
  it("forwards the conditions GET", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ conditions: [] }, { status: 200 }));
    const response = await proxyCreditOpsRequest(
      request("/api/v1/cases/case-1/conditions"),
      seg("/api/v1/cases/case-1/conditions"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(200);
  });

  it("reconstructs a create-condition body with an optional due date", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 201 }));
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/conditions", {
        conditionText: "  Bổ sung vốn đối ứng.  ",
        dueDate: "2026-08-01",
      }),
      seg("/api/v1/cases/case-1/conditions"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(201);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({ conditionText: "Bổ sung vốn đối ứng.", dueDate: "2026-08-01" }),
    );
  });

  it.each([
    { name: "missing conditionText", body: { owner: "x" } },
    { name: "extra key", body: { conditionText: "x", severity: "high" } },
  ])("rejects an invalid create-condition body: $name", async ({ body }) => {
    const fetcher = vi.fn();
    const response = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/conditions", body),
      seg("/api/v1/cases/case-1/conditions"),
      { fetcher, upstreamBaseUrl },
    );
    expect(response.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("reconstructs a condition transition and rejects an unknown status", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 200 }));
    const ok = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/conditions/cond-1/transition", {
        toStatus: "WAIVED_BY_HUMAN",
        rationale: " Miễn trừ có thẩm quyền. ",
      }),
      seg("/api/v1/cases/case-1/conditions/cond-1/transition"),
      { fetcher, upstreamBaseUrl },
    );
    expect(ok.status).toBe(200);
    expect(fetcher.mock.calls[0][1].body).toBe(
      JSON.stringify({ toStatus: "WAIVED_BY_HUMAN", rationale: "Miễn trừ có thẩm quyền." }),
    );

    const rejected = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/conditions/cond-1/transition", {
        toStatus: "APPROVED",
      }),
      seg("/api/v1/cases/case-1/conditions/cond-1/transition"),
      { fetcher, upstreamBaseUrl },
    );
    expect(rejected.status).toBe(422);
  });

  it("forwards the conditions confirm POST with an exactly-empty body", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 200 }));
    const ok = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/conditions/confirm", {}),
      seg("/api/v1/cases/case-1/conditions/confirm"),
      { fetcher, upstreamBaseUrl },
    );
    expect(ok.status).toBe(200);
    expect(fetcher.mock.calls[0][1].body).toBe("{}");

    const rejected = await proxyCreditOpsRequest(
      stageMutation("/api/v1/cases/case-1/conditions/confirm", { rationale: "x" }),
      seg("/api/v1/cases/case-1/conditions/confirm"),
      { fetcher, upstreamBaseUrl },
    );
    expect(rejected.status).toBe(422);
  });
});
