import { describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  CreditOpsApiClient,
  getVietnameseApiError,
} from "../../lib/api/client";

function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function clientWith(fetcher: typeof fetch): CreditOpsApiClient {
  return new CreditOpsApiClient("/api/creditops", fetcher, () => null);
}

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected promise to reject");
}

describe("429 Too Many Requests", () => {
  it("exposes retryAfterSeconds parsed from an integer Retry-After header", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(429, { code: "RATE_LIMITED" }, { "retry-after": "30" }),
      );
    const error = await rejection(clientWith(fetcher).listCases());

    expect(error).toBeInstanceOf(ApiClientError);
    expect((error as ApiClientError).status).toBe(429);
    expect((error as ApiClientError).retryAfterSeconds).toBe(30);
  });

  it("yields a null retryAfterSeconds when the header is absent", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(429, {}));
    const error = await rejection(clientWith(fetcher).listCases());

    expect((error as ApiClientError).retryAfterSeconds).toBeNull();
  });

  it("yields a null retryAfterSeconds for a non-numeric header (HTTP-date form)", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse(429, {}, { "retry-after": "Wed, 21 Oct 2026 07:28:00 GMT" }),
    );
    const error = await rejection(clientWith(fetcher).listCases());

    expect((error as ApiClientError).retryAfterSeconds).toBeNull();
  });

  it("never auto-retries a rate-limited request", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(jsonResponse(429, {}, { "retry-after": "5" }));

    await rejection(clientWith(fetcher).listCases());

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("does not populate retryAfterSeconds for other error statuses", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(jsonResponse(500, {}, { "retry-after": "5" }));
    const error = await rejection(clientWith(fetcher).listCases());

    expect((error as ApiClientError).status).toBe(500);
    expect((error as ApiClientError).retryAfterSeconds).toBeNull();
  });
});

describe("getVietnameseApiError: 429", () => {
  it("tells the user how long to wait when the seconds are known", () => {
    const error = new ApiClientError(429, "RATE_LIMITED", "", false, 30);

    expect(getVietnameseApiError(error)).toMatch(/30 giây/);
  });

  it("gives a generic wait message when the seconds are unknown", () => {
    const error = new ApiClientError(429, "RATE_LIMITED", "", false, null);

    expect(getVietnameseApiError(error)).toMatch(/đợi/i);
  });

  it("does not throw for a rate-limit-shaped DirectStorageError-like input", () => {
    const directStorageLike = Object.assign(new Error("rate limited"), {
      name: "DirectStorageError",
      status: 429,
    });

    expect(getVietnameseApiError(directStorageLike)).toMatch(/đợi/i);
  });
});

describe("getVietnameseApiError: 404", () => {
  it("uses a single message that does not distinguish missing resource from unassigned case", () => {
    const missing = new ApiClientError(404, "NOT_FOUND", "", false);
    const unassigned = new ApiClientError(404, "CASE_NOT_ACCESSIBLE", "", false);

    expect(getVietnameseApiError(missing)).toBe(
      "Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
    );
    expect(getVietnameseApiError(unassigned)).toBe(
      "Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
    );
  });
});

describe("getVietnameseApiError: existing statuses are unchanged", () => {
  it.each([
    [401, "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại."],
    [403, "Bạn không có quyền thực hiện thao tác này trên hồ sơ."],
    [409, "Dữ liệu đã thay đổi hoặc thao tác bị trùng. Vui lòng tải lại."],
    [413, "Tài liệu vượt quá dung lượng được phép."],
    [415, "Định dạng tài liệu chưa được hỗ trợ."],
    [422, "Thông tin tài liệu chưa hợp lệ. Vui lòng kiểm tra và thử lại."],
  ])("keeps the %i message stable", (status, message) => {
    const error = new ApiClientError(status, "CODE", "", false);
    expect(getVietnameseApiError(error)).toBe(message);
  });
});

describe("202 Accepted exposure", () => {
  it("requestWithStatus surfaces the HTTP status alongside the parsed body", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(202, { taskId: "task-1", status: "PENDING" }),
      );
    const result = await clientWith(fetcher).requestWithStatus(
      "/api/v1/upload-intents/intent-1/complete",
      { method: "POST", body: "{}" },
    );

    expect(result.status).toBe(202);
    expect(result.body).toMatchObject({ taskId: "task-1", status: "PENDING" });
  });

  it("still throws ApiClientError through requestWithStatus for non-ok statuses", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(jsonResponse(500, { code: "UPSTREAM_ERROR" }));

    const error = await rejection(
      clientWith(fetcher).requestWithStatus("/api/v1/cases"),
    );
    expect(error).toBeInstanceOf(ApiClientError);
  });

  it("leaves existing typed methods returning only the parsed body for a 200", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [],
        nextCursor: null,
        capabilities: { canCreateCase: true },
      }),
    );

    const result = await clientWith(fetcher).listCases();
    expect(result).toMatchObject({ items: [], nextCursor: null });
  });
});
