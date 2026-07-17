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
