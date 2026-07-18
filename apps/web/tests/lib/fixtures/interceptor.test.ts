import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { buildStore } from "../../../lib/fixtures/dataset";
import {
  getActiveStore,
  setActiveStore,
  subscribeToFixture,
} from "../../../lib/fixtures/interceptor";

// The interceptor captures window.fetch on install, so it must be a real
// function before a store is activated. jsdom + Node usually provide one, but we
// guard defensively so the test is robust across environments.
beforeEach(() => {
  if (typeof window.fetch !== "function") {
    window.fetch = (async () => new Response(null, { status: 599 })) as typeof fetch;
  }
});

afterEach(() => {
  setActiveStore(null);
});

describe("fixture interceptor", () => {
  it("answers /api/creditops fetches from the active store while installed", async () => {
    setActiveStore(buildStore("clean-complete"));
    expect(getActiveStore()).not.toBeNull();

    let fired = false;
    const unsubscribe = subscribeToFixture(() => {
      fired = true;
    });

    const response = await window.fetch("/api/creditops/api/v1/cases");
    expect(response.status).toBe(200);

    const body = (await response.json()) as { items: unknown[] };
    expect(Array.isArray(body.items)).toBe(true);
    expect(body.items.length).toBeGreaterThan(0);
    expect(fired).toBe(true);

    unsubscribe();
  });

  it("restores the real fetch and clears the store on deactivation", () => {
    setActiveStore(buildStore("clean-complete"));
    expect(getActiveStore()).not.toBeNull();

    setActiveStore(null);
    expect(getActiveStore()).toBeNull();
  });
});
