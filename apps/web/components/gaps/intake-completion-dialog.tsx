"use client";

import Link from "next/link";
import React, { useEffect, useId, useRef, useState } from "react";

import { getIntakeIncompleteReasons, getVietnameseApiError } from "../../lib/api/client";
import type { IntakeCompletionResultDto } from "../../lib/api/contracts";
import styles from "./intake-completion-dialog.module.css";

export interface IntakeCompletionDialogProps {
  open: boolean;
  onClose: () => void;
  // Performs the real POST /intake-completion. Resolves with the handoff on
  // success; throws on failure (a 409 INTAKE_INCOMPLETE carries its unresolved
  // reasons on the thrown error's details).
  onComplete: () => Promise<IntakeCompletionResultDto>;
  // Optional: notify the parent after a successful completion so it can refetch.
  onCompleted?: (result: IntakeCompletionResultDto) => void;
  caseId: string;
  openGapCount: number; // provisional+formal gaps still open
  caseVersion: number;
  canCompleteIntake: boolean;
}

const HANDOFF_STATE_LABELS: Record<string, string> = {
  READY_FOR_SPECIALIST_REVIEW: "Sẵn sàng cho chuyên viên thẩm định",
};

// Accessible modal dialog implemented manually (no new dependency): traps
// focus on open, restores focus to whatever was focused before opening, and
// closes on Escape or the cancel action. Completion is never optimistic — the
// success panel appears only after the server confirms the handoff.
export function IntakeCompletionDialog({
  open,
  onClose,
  onComplete,
  onCompleted,
  caseId,
  openGapCount,
  caseVersion,
  canCompleteIntake,
}: IntakeCompletionDialogProps) {
  const headingId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState(false);
  const [reasons, setReasons] = useState<string[] | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<IntakeCompletionResultDto | null>(null);

  useEffect(() => {
    if (open) {
      previouslyFocused.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setAcknowledged(false);
      setPending(false);
      setReasons(null);
      setSubmitError(null);
      setResult(null);
      dialogRef.current?.focus();
    } else {
      previouslyFocused.current?.focus();
      previouslyFocused.current = null;
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      // Keep Tab/Shift+Tab focus cycling inside the dialog so aria-modal is
      // honest: the modal is rendered inline (not portalled), so without this
      // trap Tab would escape into background page controls (the trigger,
      // CaseNav links). Standard manual dialog behavior — no new dependency.
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const inDialog = active instanceof Node && dialog.contains(active);
      if (event.shiftKey) {
        if (!inDialog || active === first || active === dialog) {
          event.preventDefault();
          last.focus();
        }
      } else if (!inDialog || active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  async function handleConfirm() {
    if (pending) return;
    setPending(true);
    setReasons(null);
    setSubmitError(null);
    try {
      const completion = await onComplete();
      setResult(completion);
      onCompleted?.(completion);
    } catch (requestError) {
      const incompleteReasons = getIntakeIncompleteReasons(requestError);
      if (incompleteReasons) {
        setReasons(incompleteReasons);
      } else {
        setSubmitError(getVietnameseApiError(requestError));
      }
    } finally {
      setPending(false);
    }
  }

  const confirmDisabled = !acknowledged || pending;

  return (
    <div className={styles.overlay}>
      <div
        aria-labelledby={headingId}
        aria-modal="true"
        className={styles.dialog}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <h2 id={headingId}>Hoàn tất bộ hồ sơ tiếp nhận</h2>

        {result ? (
          <div className={styles.success} role="status">
            <p className={styles.body}>
              Đã tạo gói bàn giao tại phiên bản hồ sơ {result.caseVersion}.
            </p>
            <p className={styles.metaLine}>
              Mã gói bàn giao: <strong>{result.handoffId}</strong>
            </p>
            <p className={styles.metaLine}>
              Trạng thái: {HANDOFF_STATE_LABELS[result.state] ?? result.state}
            </p>
            <div className={styles.actions}>
              <button className="button button-secondary" onClick={onClose} type="button">
                Đóng
              </button>
              <Link className="button button-primary" href={`/ho-so/${caseId}/ban-giao`}>
                Mở bàn giao
              </Link>
            </div>
          </div>
        ) : (
          <>
            <p className={styles.body}>
              Hoàn tất tiếp nhận sẽ đóng băng hồ sơ tại phiên bản {caseVersion}. Các khoảng
              trống chứng cứ chính thức sẽ được ghi nhận và một gói bàn giao sẽ được tạo cho
              chuyên viên rà soát độc lập. Đây không phải quyết định tín dụng.
            </p>
            {openGapCount > 0 && (
              <p className={styles.warning} role="status">
                Còn {openGapCount} khoảng trống chứng cứ chưa giải quyết.
              </p>
            )}
            {reasons ? (
              <div className={styles.reasons} role="alert">
                <p className={styles.reasonsHeading}>
                  Hồ sơ tiếp nhận chưa hoàn tất; các mục chưa xử lý:
                </p>
                {reasons.length > 0 ? (
                  <ul className={styles.reasonsList}>
                    {reasons.map((reason, index) => (
                      // eslint-disable-next-line react/no-array-index-key
                      <li key={index}>{reason}</li>
                    ))}
                  </ul>
                ) : (
                  <p className={styles.body}>
                    Vui lòng rà soát lại toàn bộ dữ kiện và khoảng trống chứng cứ trước khi hoàn
                    tất.
                  </p>
                )}
              </div>
            ) : null}
            {submitError ? (
              <p className={styles.error} role="alert">
                {submitError}
              </p>
            ) : null}
            {canCompleteIntake ? (
              <label className={styles.checkboxLabel}>
                <input
                  checked={acknowledged}
                  disabled={pending}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                  type="checkbox"
                />
                Tôi xác nhận đã rà soát toàn bộ tài liệu và khoảng trống chứng cứ.
              </label>
            ) : (
              <p className={styles.note} role="note">
                Bạn không có quyền hoàn tất tiếp nhận hồ sơ này.
              </p>
            )}
            <div className={styles.actions}>
              <button
                className="button button-secondary"
                disabled={pending}
                onClick={onClose}
                type="button"
              >
                Hủy
              </button>
              {canCompleteIntake && (
                <button
                  aria-busy={pending}
                  className="button button-primary"
                  disabled={confirmDisabled}
                  onClick={() => void handleConfirm()}
                  type="button"
                >
                  {pending ? "Đang hoàn tất…" : "Hoàn tất tiếp nhận"}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
