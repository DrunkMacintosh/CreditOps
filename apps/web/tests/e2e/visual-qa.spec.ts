import { test } from "@playwright/test";

// Visual QA capture (run with --project=desktop-chromium). Screenshots land in
// the scratchpad for review; each screen is driven by an active synthetic
// scenario so the real workspaces render fixture data.

const CASE = "11111111-1111-4111-8111-111111111111";
// Repo-relative, gitignored artifacts dir so this is portable across machines.
const OUT = "test-results/visual-qa";

async function seed(page: import("@playwright/test").Page, id: string) {
  await page.addInitScript((s) => sessionStorage.setItem("creditops.fixture.scenario", s), id);
}

const SHOTS: { name: string; scenario: string; path: string; wait?: string }[] = [
  { name: "cong-viec", scenario: "clean-complete", path: "/cong-viec" },
  { name: "intake", scenario: "clean-complete", path: `/ho-so/${CASE}/tiep-nhan` },
  { name: "document-review", scenario: "clean-complete", path: `/ho-so/${CASE}/tai-lieu/doc-bctc-2025` },
  { name: "conflicts", scenario: "conflicting-facts", path: `/ho-so/${CASE}/doi-chieu` },
  { name: "risk-review", scenario: "risk-challenge", path: `/ho-so/${CASE}/rui-ro` },
  { name: "conditions", scenario: "pending-condition", path: `/ho-so/${CASE}/dieu-kien-giai-ngan` },
  { name: "disbursement", scenario: "execution-unknown", path: `/ho-so/${CASE}/giai-ngan` },
  { name: "policy-unavailable", scenario: "policy-unavailable", path: `/ho-so/${CASE}/phap-che` },
  { name: "underwriting", scenario: "clean-complete", path: `/ho-so/${CASE}/tham-dinh` },
  { name: "underwriting-stale", scenario: "downstream-stale", path: `/ho-so/${CASE}/tham-dinh` },
  { name: "tong-hop", scenario: "clean-complete", path: `/ho-so/${CASE}/tong-hop` },
];

for (const shot of SHOTS) {
  test(`shot:${shot.name}`, async ({ page }) => {
    await seed(page, shot.scenario);
    await page.goto(shot.path);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/qa-${shot.name}.png`, fullPage: true });
  });
}

test("shot:switcher-open", async ({ page }) => {
  await seed(page, "clean-complete");
  await page.goto(`/ho-so/${CASE}/tiep-nhan`);
  await page.getByRole("button", { name: /Kịch bản kiểm thử/ }).click();
  await page.getByRole("button", { name: /Dữ kiện mâu thuẫn/ }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/qa-switcher-open.png` });
});

test("shot:intake-mobile", async ({ page }) => {
  await seed(page, "clean-complete");
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/ho-so/${CASE}/tiep-nhan`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/qa-intake-mobile.png`, fullPage: true });
});
