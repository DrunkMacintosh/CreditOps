"use client";

import React, { useEffect, useId, useRef, useState } from "react";

import styles from "./intake-completion-dialog.module.css";

export interface IntakeCompletionDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
  openGapCount: number; // provisional+formal gaps still open
  caseVersion: number;
  canCompleteIntake: boolean;
  submitUnavailableReason?: string; // when set, confirm is disabled and the reason is shown
}

// Accessible modal dialog implemented manually (no new dependency): traps
// focus on open, restores focus to whatever was focused before opening, and
// closes on Escape or the cancel action. Never invokes onConfirm on its own.
export function IntakeCompletionDialog({
  open,
  onClose,
  onConfirm,
  openGapCount,
  caseVersion,
  canCompleteIntake,
  submitUnavailableReason,
}: IntakeCompletionDialogProps) {
  const headingId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);

  useEffect(() => {
    if (open) {
      previouslyFocused.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setAcknowledged(false);
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

  const confirmDisabled =
    !acknowledged || Boolean(submitUnavailableReason);

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
        {canCompleteIntake ? (
          <>
            <label className={styles.checkboxLabel}>
              <input
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
                type="checkbox"
              />
              Tôi xác nhận đã rà soát toàn bộ tài liệu và khoảng trống chứng cứ.
            </label>
            {submitUnavailableReason && (
              <p className={styles.note} role="note">
                {submitUnavailableReason}
              </p>
            )}
          </>
        ) : (
          <p className={styles.note} role="note">
            Bạn không có quyền hoàn tất tiếp nhận hồ sơ này.
          </p>
        )}
        <div className={styles.actions}>
          <button className="button button-secondary" onClick={onClose} type="button">
            Hủy
          </button>
          {canCompleteIntake && (
            <button
              className="button button-primary"
              disabled={confirmDisabled}
              onClick={() => void onConfirm()}
              type="button"
            >
              Hoàn tất tiếp nhận
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
