import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { ScenarioSwitcher } from "../../components/scenario/scenario-switcher";
import { ScenarioProvider } from "../../lib/fixtures/scenario-context";
import { SCENARIOS } from "../../lib/fixtures/scenarios";

// scenario-context.tsx and the switcher use the automatic JSX runtime (no
// `import React`). Vitest's classic esbuild transform emits React.createElement,
// so expose React globally for those modules under test.
Object.assign(globalThis, { React });

function renderSwitcher() {
  return render(
    <ScenarioProvider>
      <ScenarioSwitcher />
    </ScenarioProvider>,
  );
}

afterEach(() => {
  window.sessionStorage.clear();
});

describe("ScenarioSwitcher", () => {
  it("renders the launcher with the Vietnamese label", () => {
    renderSwitcher();

    expect(
      screen.getByRole("button", { name: /Kịch bản kiểm thử/ }),
    ).toBeInTheDocument();
  });

  it("opens a dialog showing the SYNTHETIC TEST FIXTURE badge and all 12 scenarios", async () => {
    renderSwitcher();

    await userEvent.click(
      screen.getByRole("button", { name: /Kịch bản kiểm thử/ }),
    );

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("SYNTHETIC TEST FIXTURE")).toBeVisible();

    // Every scenario title is listed.
    for (const scenario of SCENARIOS) {
      expect(within(dialog).getByText(scenario.title)).toBeInTheDocument();
    }
    expect(SCENARIOS).toHaveLength(12);
  });

  it("labels the fixture as synthetic, not live agent inference", async () => {
    renderSwitcher();
    await userEvent.click(
      screen.getByRole("button", { name: /Kịch bản kiểm thử/ }),
    );

    expect(
      screen.getByText(/không phải suy luận agent trực tiếp/i),
    ).toBeInTheDocument();
  });

  it("shows a scenario's expected result and a status badge with TEXT when activated", async () => {
    renderSwitcher();
    await userEvent.click(
      screen.getByRole("button", { name: /Kịch bản kiểm thử/ }),
    );

    // Activate scenario 2 (missing-documents) — deterministic "pass" via evaluator.
    const missing = SCENARIOS.find((s) => s.id === "missing-documents")!;
    await userEvent.click(
      screen.getByRole("button", { name: new RegExp(missing.title) }),
    );

    // Expected-result text is rendered in the detail panel.
    expect(screen.getByText(missing.expectedResult)).toBeInTheDocument();

    // The assertion status is announced with a Vietnamese TEXT label, not colour
    // alone — missing-documents evaluates to "pass" → "ĐẠT".
    const live = screen
      .getByText(missing.expectedResult)
      .closest("section")!;
    expect(within(live).getByText("ĐẠT")).toBeInTheDocument();
  });

  it("marks the active scenario with aria-current", async () => {
    renderSwitcher();
    await userEvent.click(
      screen.getByRole("button", { name: /Kịch bản kiểm thử/ }),
    );

    const clean = SCENARIOS[0];
    const item = screen.getByRole("button", { name: new RegExp(clean.title) });
    await userEvent.click(item);

    expect(
      screen.getByRole("button", { name: new RegExp(clean.title) }),
    ).toHaveAttribute("aria-current", "true");
  });

  it("renders test control buttons with their effects and runs them without throwing", async () => {
    renderSwitcher();
    await userEvent.click(
      screen.getByRole("button", { name: /Kịch bản kiểm thử/ }),
    );

    // Scenario 8 (unauthorized-access) has two test controls.
    const scenario = SCENARIOS.find((s) => s.id === "unauthorized-access")!;
    await userEvent.click(
      screen.getByRole("button", { name: new RegExp(scenario.title) }),
    );

    for (const control of scenario.testControls) {
      const button = screen.getByRole("button", { name: control.label });
      expect(button).toBeInTheDocument();
      expect(screen.getByText(control.effect)).toBeInTheDocument();
      // Clicking drives a deterministic synthetic transition; must not throw.
      await userEvent.click(button);
    }
  });

  it("renders evidence-ref chips for a scenario that has them", async () => {
    renderSwitcher();
    await userEvent.click(
      screen.getByRole("button", { name: /Kịch bản kiểm thử/ }),
    );

    const scenario = SCENARIOS.find((s) => s.evidenceRefs.length > 0)!;
    await userEvent.click(
      screen.getByRole("button", { name: new RegExp(scenario.title) }),
    );

    for (const ref of scenario.evidenceRefs) {
      expect(screen.getByText(ref)).toBeInTheDocument();
    }
  });

  it("provides deactivate and reset controls plus a deep link to the focus workspace", async () => {
    renderSwitcher();
    await userEvent.click(
      screen.getByRole("button", { name: /Kịch bản kiểm thử/ }),
    );

    const scenario = SCENARIOS.find((s) => s.id === "missing-documents")!;
    await userEvent.click(
      screen.getByRole("button", { name: new RegExp(scenario.title) }),
    );

    expect(
      screen.getByRole("button", { name: "Tắt kịch bản" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "Đặt lại" })).toBeEnabled();

    const link = screen.getByRole("link", { name: /Mở màn hình bước này/ });
    expect(link).toHaveAttribute(
      "href",
      expect.stringContaining(`/${scenario.focusSection}`),
    );
  });

  it("closes on Escape and returns focus to the launcher", async () => {
    renderSwitcher();
    const launcher = screen.getByRole("button", {
      name: /Kịch bản kiểm thử/,
    });
    await userEvent.click(launcher);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(launcher).toHaveFocus();
  });
});
