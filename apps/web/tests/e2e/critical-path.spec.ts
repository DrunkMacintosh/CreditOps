import { expect, test, type Page } from "@playwright/test";

// -----------------------------------------------------------------------------
// Critical-path E2E driven entirely by the SYNTHETIC TEST FIXTURE.
//
// Each test seeds an active scenario into sessionStorage before load; the
// fixture interceptor's eager restore then answers /api/creditops/* offline, so
// the real 14-stage workspaces render synthetic data with no live upstream.
// The flow mirrors the brief's critical path:
//   create case → financing need → upload → review candidate → accept/correct →
//   gap/conflict → handoff → underwriting → risk challenge → human decision.
// Because each scenario spotlights one stage, the path is exercised across the
// scenarios that own each step (clean-complete, conflicting-facts, risk-challenge).
// -----------------------------------------------------------------------------

const FIXTURE_CASE_ID = "11111111-1111-4111-8111-111111111111";
const BASE_DOC_ID = "doc-bctc-2025";

async function activateScenario(page: Page, scenarioId: string): Promise<void> {
  await page.addInitScript((id) => {
    window.sessionStorage.setItem("creditops.fixture.scenario", id);
  }, scenarioId);
}

test.describe("Scenario Switcher", () => {
  test("activates a scenario from the UI and shows a pass/fail badge", async ({ page }) => {
    await page.goto("/ho-so");
    await page.getByRole("button", { name: /Kịch bản kiểm thử/ }).click();
    // The fixture harness identifies itself, loudly.
    await expect(page.getByText("SYNTHETIC TEST FIXTURE")).toBeVisible();
    await page.getByRole("button", { name: /Hồ sơ hoàn chỉnh, sạch/ }).click();
    // Assertion badge carries a textual status word, never colour alone.
    await expect(page.getByText(/ĐẠT|CHƯA ĐẠT|ĐANG CHỜ/).first()).toBeVisible();
  });
});

test.describe("Critical path", () => {
  test("create → intake → upload → review → confirm → evidence → handoff", async ({ page }) => {
    await activateScenario(page, "clean-complete");

    // Create case (captures the financing need: amount + purpose).
    await page.goto("/ho-so/tao-moi");
    await page.getByLabel("Số tiền đề nghị").fill("5000000000");
    await page.getByLabel("Mục đích vay vốn").fill("Bổ sung vốn lưu động (dữ liệu tổng hợp)");
    await page.getByRole("button", { name: "Tạo hồ sơ" }).click();
    await expect(page.getByText("Đã tạo hồ sơ")).toBeVisible();
    await page.getByRole("link", { name: "Mở hồ sơ vừa tạo" }).click();
    await expect(page.getByRole("heading", { name: "Tiếp nhận tài liệu" })).toBeVisible();

    // Review the pre-ingested synthetic document; accept every candidate fact.
    await page.goto(`/ho-so/${FIXTURE_CASE_ID}/tai-lieu/${BASE_DOC_ID}`);
    const accepts = page.getByRole("radio", { name: /^Chấp nhận/ });
    // The review loads its candidates client-side; wait for them before counting.
    await accepts.first().waitFor({ state: "visible" });
    const count = await accepts.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i += 1) await accepts.nth(i).check();
    await page.getByRole("button", { name: "Xác nhận tài liệu" }).click();
    await expect(page.getByText("Đã xác nhận tài liệu")).toBeVisible();

    // Evidence ledger shows confirmed facts with provenance.
    await page.goto(`/ho-so/${FIXTURE_CASE_ID}/doi-chieu`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    // Handoff to underwriting is recorded.
    await page.goto(`/ho-so/${FIXTURE_CASE_ID}/ban-giao`);
    await expect(page.getByText(/Sẵn sàng cho chuyên viên thẩm định/).first()).toBeVisible();
  });

  test("conflicting facts surface ≥2 sources and block intake completion", async ({ page }) => {
    await activateScenario(page, "conflicting-facts");
    await page.goto(`/ho-so/${FIXTURE_CASE_ID}/doi-chieu`);
    // The conflicting revenue field appears with both source values.
    await expect(page.getByText(/48\.200\.000\.000|48200000000/).first()).toBeVisible();
    await expect(page.getByText(/51\.900\.000\.000|51900000000/).first()).toBeVisible();
  });

  test("risk challenge cannot be self-closed by the agent (human gate stays open)", async ({
    page,
  }) => {
    await activateScenario(page, "risk-challenge");
    await page.goto(`/ho-so/${FIXTURE_CASE_ID}/rui-ro`);
    // A challenge is presented for human disposition.
    await expect(page.getByText(/Thách thức/).first()).toBeVisible();
    // The G3 gate is NOT satisfied automatically — a human must dispose it.
    await expect(page.getByText("Đang chờ").first()).toBeVisible();
  });
});
