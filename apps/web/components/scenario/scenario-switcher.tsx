"use client";

// -----------------------------------------------------------------------------
// ScenarioSwitcher — the flagship UI for the synthetic test-fixture harness.
//
// A floating, collapsible instrument (NOT a chatbot) that lets a QA tester pick
// one of the 12 synthetic scenarios and drive the real 14-stage UI with
// deterministic fixture data. It reads everything it shows from useScenario();
// it invents no state of its own beyond open/closed + which detail is expanded.
//
// It labels itself, loudly and permanently, as a SYNTHETIC TEST FIXTURE: the
// data is not live agent inference and the panel never masquerades as one.
// -----------------------------------------------------------------------------

import Link from "next/link";
import React, { useCallback, useEffect, useId, useRef, useState } from "react";

import { useScenario } from "../../lib/fixtures/scenario-context";
import { SCENARIOS } from "../../lib/fixtures/scenarios";
import type { AssertionStatus, ScenarioDefinition } from "../../lib/fixtures/types";

import styles from "./scenario-switcher.module.css";

// Vietnamese status vocabulary + a distinct glyph per status, so status is
// never conveyed by colour alone (WCAG 1.4.1).
const STATUS_META: Record<AssertionStatus, { label: string; glyph: string; className: string }> = {
  pass: { label: "ĐẠT", glyph: "✓", className: styles.statusPass },
  fail: { label: "CHƯA ĐẠT", glyph: "✕", className: styles.statusFail },
  pending: { label: "ĐANG CHỜ", glyph: "◔", className: styles.statusPending },
};

function focusHref(scenario: ScenarioDefinition, caseId: string): string {
  if (scenario.focusSection === "tai-lieu" && scenario.focusDocumentId) {
    return `/ho-so/${caseId}/tai-lieu/${scenario.focusDocumentId}`;
  }
  return `/ho-so/${caseId}/${scenario.focusSection}`;
}

export function ScenarioSwitcher() {
  const {
    activeScenarioId,
    activeScenario,
    fixtureCaseId,
    assertion,
    activate,
    deactivate,
    reset,
    runControl,
  } = useScenario();

  const [open, setOpen] = useState(false);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const noteId = useId();

  const close = useCallback(() => {
    setOpen(false);
    // Return focus to the launcher so keyboard users are not stranded.
    launcherRef.current?.focus();
  }, []);

  // Escape closes the panel and restores focus to the launcher.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  // On open, move focus into the panel (its close control).
  useEffect(() => {
    if (open) panelRef.current?.querySelector<HTMLButtonElement>("[data-autofocus]")?.focus();
  }, [open]);

  const status: AssertionStatus = assertion?.status ?? "pending";
  const statusMeta = STATUS_META[status];

  return (
    <>
      <button
        ref={launcherRef}
        type="button"
        className={styles.launcher}
        aria-haspopup="dialog"
        aria-expanded={open}
        // Stable accessible name so the control is reachable even where the
        // visible text label is hidden (narrow viewports); the active status is
        // appended so screen-reader users hear it too.
        aria-label={
          activeScenario
            ? `Kịch bản kiểm thử — kịch bản ${activeScenario.ordinal}, trạng thái ${statusMeta.label}`
            : "Kịch bản kiểm thử"
        }
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true" className={styles.launcherFlask}>
          ⚗
        </span>
        <span className={styles.launcherLabel}>Kịch bản kiểm thử</span>
        {activeScenario ? (
          <span className={styles.launcherState}>
            <span aria-hidden="true" className={styles.launcherOrdinal}>
              {activeScenario.ordinal}
            </span>
            <span className={`${styles.launcherStatus} ${statusMeta.className}`}>
              <span aria-hidden="true">{statusMeta.glyph}</span>
              {statusMeta.label}
            </span>
            <span className={styles.srOnly}>
              {`Kịch bản ${activeScenario.ordinal} đang chạy, trạng thái ${statusMeta.label}`}
            </span>
          </span>
        ) : null}
      </button>

      {open ? (
        <div className={styles.overlay}>
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="false"
            aria-labelledby={titleId}
            aria-describedby={noteId}
            className={styles.panel}
          >
            <header className={styles.header}>
              <div className={styles.headerTop}>
                <span className={styles.fixtureBadge}>
                  <span aria-hidden="true" className={styles.fixtureFlask}>
                    ⚗
                  </span>
                  SYNTHETIC TEST FIXTURE
                </span>
                <button
                  type="button"
                  data-autofocus
                  className={styles.closeButton}
                  onClick={close}
                  aria-label="Đóng bảng kịch bản kiểm thử"
                >
                  <span aria-hidden="true">✕</span>
                </button>
              </div>
              <h2 id={titleId} className={styles.title}>
                Bộ chuyển kịch bản kiểm thử
              </h2>
              <p id={noteId} className={styles.note}>
                Dữ liệu tổng hợp, xác định trước — <strong>không phải suy luận agent trực tiếp</strong>.
                Dùng để lái giao diện thật với đầu vào kiểm thử.
              </p>
            </header>

            <div className={styles.body}>
              <section aria-label="Danh sách kịch bản" className={styles.listSection}>
                <div className={styles.listHeader}>
                  <h3 className={styles.sectionLabel}>12 kịch bản</h3>
                  <div className={styles.listActions}>
                    <button
                      type="button"
                      className={styles.ghostButton}
                      onClick={deactivate}
                      disabled={!activeScenarioId}
                    >
                      Tắt kịch bản
                    </button>
                    <button
                      type="button"
                      className={styles.ghostButton}
                      onClick={reset}
                      disabled={!activeScenarioId}
                    >
                      Đặt lại
                    </button>
                  </div>
                </div>
                <ul className={styles.scenarioList}>
                  {SCENARIOS.map((scenario) => {
                    const isActive = scenario.id === activeScenarioId;
                    return (
                      <li key={scenario.id}>
                        <button
                          type="button"
                          className={styles.scenarioItem}
                          aria-current={isActive ? "true" : undefined}
                          data-active={isActive || undefined}
                          onClick={() => activate(scenario.id)}
                        >
                          <span aria-hidden="true" className={styles.scenarioOrdinal}>
                            {scenario.ordinal}
                          </span>
                          <span className={styles.scenarioTitle}>
                            <span className={styles.srOnly}>{`Kịch bản ${scenario.ordinal}: `}</span>
                            {scenario.title}
                          </span>
                          {isActive ? (
                            <span aria-hidden="true" className={styles.scenarioActiveMark}>
                              ●
                            </span>
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>

              {activeScenario ? (
                <section aria-label="Chi tiết kịch bản" className={styles.detailSection}>
                  <h3 className={styles.detailTitle}>
                    <span aria-hidden="true" className={styles.detailOrdinal}>
                      {activeScenario.ordinal}
                    </span>
                    {activeScenario.title}
                  </h3>

                  <div aria-live="polite" className={styles.assertionLive}>
                    <span className={`${styles.assertionBadge} ${statusMeta.className}`}>
                      <span aria-hidden="true" className={styles.assertionGlyph}>
                        {statusMeta.glyph}
                      </span>
                      <span className={styles.assertionLabel}>{statusMeta.label}</span>
                    </span>
                    <span className={styles.assertionActual}>
                      {assertion?.actual ?? "Chưa có kết quả — hãy lái giao diện hoặc chạy điều khiển."}
                    </span>
                  </div>

                  <dl className={styles.detailGrid}>
                    <div className={styles.detailRow}>
                      <dt>Trạng thái ban đầu</dt>
                      <dd>{activeScenario.initialState}</dd>
                    </div>
                    <div className={styles.detailRow}>
                      <dt>Agent đang kiểm thử</dt>
                      <dd>{activeScenario.agentUnderTest}</dd>
                    </div>
                    <div className={styles.detailRow}>
                      <dt>Human gate</dt>
                      <dd>{activeScenario.humanGate}</dd>
                    </div>
                    <div className={styles.detailRow}>
                      <dt>Kết quả kỳ vọng</dt>
                      <dd>{activeScenario.expectedResult}</dd>
                    </div>
                    <div className={styles.detailRow}>
                      <dt>Kết quả thực tế</dt>
                      <dd>{assertion?.actual ?? "—"}</dd>
                    </div>
                    <div className={styles.detailRow}>
                      <dt>Sự kiện kiểm toán</dt>
                      <dd>
                        <code className={styles.auditEvent}>{activeScenario.auditEvent}</code>
                      </dd>
                    </div>
                    <div className={styles.detailRow}>
                      <dt>Tham chiếu bằng chứng</dt>
                      <dd>
                        {activeScenario.evidenceRefs.length > 0 ? (
                          <ul className={styles.chipRow}>
                            {activeScenario.evidenceRefs.map((ref) => (
                              <li key={ref} className={styles.evidenceChip}>
                                {ref}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <span className={styles.emptyDash}>—</span>
                        )}
                      </dd>
                    </div>
                  </dl>

                  <div className={styles.controlsGroup}>
                    <h4 className={styles.controlsHeading}>Điều khiển kiểm thử</h4>
                    <p className={styles.controlsNote}>
                      Chuyển trạng thái tổng hợp, xác định trước — không phải suy luận agent trực tiếp.
                    </p>
                    {activeScenario.testControls.length > 0 ? (
                      <ul className={styles.controlsList}>
                        {activeScenario.testControls.map((control) => (
                          <li key={control.id} className={styles.controlItem}>
                            <button
                              type="button"
                              className={styles.controlButton}
                              onClick={() => runControl(control.id)}
                            >
                              {control.label}
                            </button>
                            <span className={styles.controlEffect}>{control.effect}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className={styles.controlsEmpty}>
                        Kịch bản này không có điều khiển — hãy lái trực tiếp trên màn hình bước.
                      </p>
                    )}
                  </div>

                  <Link
                    href={focusHref(activeScenario, fixtureCaseId)}
                    className={styles.focusLink}
                    onClick={close}
                  >
                    <span aria-hidden="true" className={styles.focusArrow}>
                      →
                    </span>
                    Mở màn hình bước này
                  </Link>
                </section>
              ) : (
                <section aria-label="Chi tiết kịch bản" className={styles.detailEmpty}>
                  <p className={styles.detailEmptyTitle}>Chưa chọn kịch bản</p>
                  <p className={styles.detailEmptyHint}>
                    Chọn một trong 12 kịch bản để nạp dữ liệu tổng hợp và xem kết quả kỳ vọng, human
                    gate và điều khiển kiểm thử.
                  </p>
                </section>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
