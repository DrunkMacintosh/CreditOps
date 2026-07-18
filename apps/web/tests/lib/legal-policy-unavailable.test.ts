import { describe, expect, it } from "vitest";

import { ApiClientError } from "../../lib/api/client";
import { getLegalErrorMessage, isLegalAssessmentNotReady } from "../../lib/api/legal";

// The policy-unavailable scenario (fixture) returns 503 POLICY_SOURCE_UNAVAILABLE.
// The legal workspace must show a bespoke fail-closed message with a next action,
// never a fabricated policy result and never the generic catch-all.
describe("legal policy-source-unavailable state", () => {
  const error = new ApiClientError(
    503,
    "POLICY_SOURCE_UNAVAILABLE",
    "",
    true,
  );

  it("maps POLICY_SOURCE_UNAVAILABLE to a specific, honest message", () => {
    const message = getLegalErrorMessage(error);
    expect(message).toMatch(/chính sách/i);
    expect(message).toMatch(/không khả dụng/i);
    // Must not fall through to the generic catch-all.
    expect(message).not.toBe("Không thể hoàn tất yêu cầu. Vui lòng thử lại.");
  });

  it("is treated as a recoverable error, not the empty 'not ready' state", () => {
    // notReady is reserved for LEGAL_ASSESSMENT_NOT_AVAILABLE; a policy outage is
    // a recoverable error (retry), so the workspace shows the retry panel.
    expect(isLegalAssessmentNotReady(error)).toBe(false);
  });
});
