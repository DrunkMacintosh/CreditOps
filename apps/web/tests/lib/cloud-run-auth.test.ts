import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearCloudRunAuthCache,
  getCloudRunServerlessAuthorization,
} from "../../lib/server/cloud-run-auth";

const audience = "https://creditops-api-abc-uc.a.run.app";
const subjectToken = "header.payload.signature";
const googleIdToken = "header.eyJleHAiOjE4OTM0NTYwMDB9.signature";

function request() {
  return new Request("https://app.invalid/api/creditops/api/v1/cases", {
    headers: { "x-vercel-oidc-token": subjectToken },
  });
}

describe("Cloud Run serverless authorization", () => {
  beforeEach(() => {
    clearCloudRunAuthCache();
    vi.stubEnv("VERCEL", "1");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("exchanges the Vercel OIDC token and requests a Google ID token for the API audience", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          access_token: "sts-access-token",
          token_type: "Bearer",
          expires_in: 3600,
        }),
      )
      .mockResolvedValueOnce(Response.json({ token: googleIdToken }));

    const token = await getCloudRunServerlessAuthorization(request(), {
      audience,
      projectNumber: "1234567890",
      poolId: "vercel",
      providerId: "vercel",
      serviceAccountEmail: "creditops-web-invoker@example.iam.gserviceaccount.com",
      fetcher,
    });

    expect(token).toBe(googleIdToken);
    expect(fetcher).toHaveBeenCalledTimes(2);
    const [stsUrl, stsInit] = fetcher.mock.calls[0];
    expect(stsUrl).toBe("https://sts.googleapis.com/v1/token");
    expect(new Headers(stsInit.headers).get("authorization")).toBeNull();
    const stsBody = new URLSearchParams(String(stsInit.body));
    expect(stsBody.get("subject_token")).toBe(subjectToken);
    expect(stsBody.get("audience")).toBe(
      "//iam.googleapis.com/projects/1234567890/locations/global/workloadIdentityPools/vercel/providers/vercel",
    );

    const [iamUrl, iamInit] = fetcher.mock.calls[1];
    expect(iamUrl).toBe(
      "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/creditops-web-invoker%40example.iam.gserviceaccount.com:generateIdToken",
    );
    expect(new Headers(iamInit.headers).get("authorization")).toBe(
      "Bearer sts-access-token",
    );
    expect(JSON.parse(String(iamInit.body))).toEqual({
      audience,
      includeEmail: true,
    });
  });

  it("caches a still-valid Google ID token without persisting the subject token", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ access_token: "sts-access-token", expires_in: 3600 }),
      )
      .mockResolvedValueOnce(Response.json({ token: googleIdToken }));

    const first = await getCloudRunServerlessAuthorization(request(), {
      audience,
      projectNumber: "1234567890",
      poolId: "vercel",
      providerId: "vercel",
      serviceAccountEmail: "creditops-web-invoker@example.iam.gserviceaccount.com",
      fetcher,
    });
    const second = await getCloudRunServerlessAuthorization(request(), {
      audience,
      projectNumber: "1234567890",
      poolId: "vercel",
      providerId: "vercel",
      serviceAccountEmail: "creditops-web-invoker@example.iam.gserviceaccount.com",
      fetcher,
    });

    expect(first).toBe(googleIdToken);
    expect(second).toBe(googleIdToken);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("fails closed when the request has no Vercel token or required configuration", async () => {
    const fetcher = vi.fn();
    await expect(
      getCloudRunServerlessAuthorization(
        new Request("https://app.invalid"),
        {
          audience,
          projectNumber: "1234567890",
          poolId: "vercel",
          providerId: "vercel",
          serviceAccountEmail: "creditops-web-invoker@example.iam.gserviceaccount.com",
          fetcher,
        },
      ),
    ).rejects.toMatchObject({
      code: "VERCEL_OIDC_TOKEN_MISSING",
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects non-Google token responses without exposing provider details", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ error: "invalid_grant" }, { status: 400 }));

    await expect(
      getCloudRunServerlessAuthorization(request(), {
        audience,
        projectNumber: "1234567890",
        poolId: "vercel",
        providerId: "vercel",
        serviceAccountEmail: "creditops-web-invoker@example.iam.gserviceaccount.com",
        fetcher,
      }),
    ).rejects.toMatchObject({ code: "STS_EXCHANGE_FAILED" });
  });
});
