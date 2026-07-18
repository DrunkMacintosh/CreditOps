import { act, renderHook } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

// scenario-context.tsx relies on the automatic JSX runtime and does not import
// React; under vitest's classic transform (tsconfig jsx:"preserve") its
// `React.createElement` resolves as a free global, so expose one here.
(globalThis as typeof globalThis & { React?: typeof React }).React = React;

import { setActiveStore } from "../../../lib/fixtures/interceptor";
import { ScenarioProvider, useScenario } from "../../../lib/fixtures/scenario-context";

// The provider mounts the module-global interceptor, which captures window.fetch
// on install; ensure a real fetch exists and start from a clean slate each test.
beforeEach(() => {
  if (typeof window.fetch !== "function") {
    window.fetch = (async () => new Response(null, { status: 599 })) as typeof fetch;
  }
  window.sessionStorage.clear();
  setActiveStore(null);
});

afterEach(() => {
  // Prevent the module-global interceptor from leaking across tests.
  setActiveStore(null);
  window.sessionStorage.clear();
});

describe("useScenario", () => {
  it("activates, runs a control, and deactivates a scenario", () => {
    const { result } = renderHook(() => useScenario(), { wrapper: ScenarioProvider });

    expect(result.current.activeScenarioId).toBeNull();
    expect(result.current.activeScenario).toBeNull();
    expect(result.current.assertion).toBeNull();

    act(() => {
      result.current.activate("clean-complete");
    });

    expect(result.current.activeScenarioId).toBe("clean-complete");
    expect(result.current.activeScenario).not.toBeNull();
    expect(result.current.activeScenario?.title).toBe("Hồ sơ hoàn chỉnh, sạch");
    expect(result.current.assertion).not.toBeNull();

    expect(() =>
      act(() => {
        result.current.runControl("cross-case");
      }),
    ).not.toThrow();

    act(() => {
      result.current.deactivate();
    });

    expect(result.current.activeScenarioId).toBeNull();
    expect(result.current.activeScenario).toBeNull();
  });
});
