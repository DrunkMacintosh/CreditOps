// -----------------------------------------------------------------------------
// Client-side fixture interceptor.
//
// While a scenario is active, this patches window.fetch (and, for uploads,
// window.XMLHttpRequest) so /api/creditops/* requests are answered by the
// in-memory FixtureStore instead of the live upstream. It is race-free: the
// patch reads a module-level store reference, so it is always in place while a
// scenario is active regardless of React effect ordering. When no scenario is
// active it is fully uninstalled and the app talks to the real backend.
//
// This is a SYNTHETIC TEST FIXTURE and never pretends to be a live backend.
// -----------------------------------------------------------------------------

import { buildStore } from "./dataset";
import { dispatch } from "./router";
import type { FixtureStore } from "./store";
import type { FixtureRequest, HttpMethod, ScenarioId } from "./types";

const BFF_PREFIX = "/api/creditops";
const STORAGE_MARKER = "__fixture_storage";

// Single source of truth for the persisted-scenario key, reused by the React
// provider so restore-on-refresh and the provider stay in lock-step.
export const FIXTURE_SESSION_KEY = "creditops.fixture.scenario";

let activeStore: FixtureStore | null = null;
let installed = false;
let realFetch: typeof fetch | null = null;
let RealXHR: typeof XMLHttpRequest | null = null;

const subscribers = new Set<() => void>();

export function getActiveStore(): FixtureStore | null {
  return activeStore;
}

export function subscribeToFixture(cb: () => void): () => void {
  subscribers.add(cb);
  return () => subscribers.delete(cb);
}

function notify(): void {
  for (const cb of subscribers) cb();
}

export function setActiveStore(store: FixtureStore | null): void {
  const wasActive = activeStore !== null;
  activeStore = store;
  if (store && !wasActive) install();
  else if (!store && wasActive) uninstall();
  notify();
}

function toRequest(url: URL, method: string, body: unknown, headers: Headers): FixtureRequest {
  const path = url.pathname.slice(BFF_PREFIX.length);
  return {
    method: method.toUpperCase() as HttpMethod,
    path,
    segments: path.split("/").filter(Boolean),
    query: url.searchParams,
    body,
    headers,
  };
}

function install(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  realFetch = window.fetch.bind(window);
  RealXHR = window.XMLHttpRequest;

  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const passthrough = realFetch as typeof fetch;
    if (!activeStore) return passthrough(input, init);

    const rawUrl =
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    let url: URL;
    try {
      url = new URL(rawUrl, window.location.origin);
    } catch {
      return passthrough(input, init);
    }
    if (!url.pathname.startsWith(BFF_PREFIX)) return passthrough(input, init);

    // Fixture storage PUT (only if it ever routes through fetch) → 200.
    if (url.pathname.includes(STORAGE_MARKER)) {
      return new Response(null, { status: 200 });
    }

    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    let body: unknown = null;
    const rawBody = init?.body;
    if (typeof rawBody === "string" && rawBody.length > 0) {
      try {
        body = JSON.parse(rawBody);
      } catch {
        body = null;
      }
    }

    const result = dispatch(toRequest(url, method, body, headers), activeStore);
    notify();
    const responseHeaders = new Headers({ "content-type": "application/json" });
    for (const [k, v] of Object.entries(result.headers ?? {})) responseHeaders.set(k, v);
    const payload = result.body === null ? null : JSON.stringify(result.body);
    return new Response(payload, { status: result.status, headers: responseHeaders });
  }) as typeof fetch;

  window.XMLHttpRequest = FixtureXHR as unknown as typeof XMLHttpRequest;
}

function uninstall(): void {
  if (!installed || typeof window === "undefined") return;
  installed = false;
  if (realFetch) window.fetch = realFetch;
  if (RealXHR) window.XMLHttpRequest = RealXHR;
  realFetch = null;
  RealXHR = null;
}

// -----------------------------------------------------------------------------
// Minimal XHR shim. During fixture mode the only XHR traffic is a SIGNED upload
// PUT to the same-origin fixture storage URL, which we resolve as an immediate
// success. Any other XHR simply succeeds too (there is none in this app during
// fixture mode). Non-fixture mode uses the real XMLHttpRequest (uninstalled).
// -----------------------------------------------------------------------------
class FixtureXHR {
  status = 0;
  readyState = 0;
  readonly upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  private aborted = false;

  open(): void {
    this.readyState = 1;
  }
  setRequestHeader(): void {}
  getResponseHeader(): string | null {
    return null;
  }
  abort(): void {
    this.aborted = true;
    this.onabort?.();
  }
  send(): void {
    // Resolve on a macrotask so callers can attach handlers first.
    setTimeout(() => {
      if (this.aborted) return;
      this.upload.onprogress?.({ lengthComputable: true, loaded: 100, total: 100 } as ProgressEvent);
      this.status = 200;
      this.readyState = 4;
      this.onload?.();
    }, 0);
  }
}

// Eager restore. Runs at module import (before any React effect fires), so a
// hard refresh with a persisted scenario has the interceptor installed and the
// store seeded BEFORE workspace components issue their first fetch. No-op on the
// server and when nothing is persisted.
if (typeof window !== "undefined") {
  try {
    const stored = window.sessionStorage.getItem(FIXTURE_SESSION_KEY);
    if (stored) setActiveStore(buildStore(stored as ScenarioId));
  } catch {
    // Corrupt/unknown persisted value — ignore and stay in live mode.
  }
}
