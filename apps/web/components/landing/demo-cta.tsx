"use client";

import Link from "next/link";
import React, { useCallback, useState } from "react";

import styles from "../../app/landing.module.css";

// The demo CTA mints a session, then redirects using its response body. That
// client-side interactivity lives in this leaf so the landing page itself can
// stay a Server Component and export its own metadata.
export function DemoCta() {
  const [startingDemo, setStartingDemo] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);

  const startDemo = useCallback(async () => {
    setDemoError(null);
    setStartingDemo(true);
    try {
      const response = await fetch("/api/demo-session", {
        method: "POST",
        headers: { accept: "application/json" },
        credentials: "include",
        cache: "no-store",
      });
      const body: unknown = await response.json().catch(() => null);
      const caseId =
        response.ok &&
        typeof body === "object" &&
        body !== null &&
        typeof (body as { caseId?: unknown }).caseId === "string"
          ? (body as { caseId: string }).caseId
          : null;
      if (caseId === null) {
        throw new Error("DEMO_SESSION_FAILED");
      }
      window.location.assign(`/ho-so/${encodeURIComponent(caseId)}/tiep-nhan`);
    } catch {
      setStartingDemo(false);
      setDemoError(
        "Không thể khởi tạo phiên demo lúc này. Vui lòng thử lại sau ít phút.",
      );
    }
  }, []);

  return (
    <>
      <div className={styles.heroCtas}>
        <button
          className={`button ${styles.ctaPrimary}`}
          disabled={startingDemo}
          onClick={() => void startDemo()}
          type="button"
        >
          {startingDemo
            ? "Đang khởi tạo phiên demo…"
            : "Trải nghiệm demo (dữ liệu tổng hợp)"}
        </button>
        <Link className={`button ${styles.ctaSecondary}`} href="/cong-viec">
          Vào hàng việc của tôi
        </Link>
      </div>
      {demoError ? (
        <p className={styles.demoError} role="alert">
          {demoError}
        </p>
      ) : null}
    </>
  );
}
