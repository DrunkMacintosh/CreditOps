"use client";

import React, { useId, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import styles from "./gates.module.css";

const MAX_RATIONALE = 4000;

interface RecordActionFormProps {
  // What the button does, in plain terms and stating the precise effect
  // (e.g. "Duyệt nội dung thông báo tín dụng"). Never a generic "Phê duyệt".
  submitLabel: string;
  // Busy-state button label.
  pendingLabel?: string;
  // Short instruction naming exactly what recording does.
  hint: string;
  // Whether to render the rationale textarea at all (a bare confirmation gate,
  // such as the disbursement-condition confirm, records no rationale).
  showRationale?: boolean;
  // Whether the rationale is mandatory (the server demands it where the gate
  // policy requires it; the form blocks an empty submit before any API call).
  rationaleRequired?: boolean;
  rationaleLabel?: string;
  // Maps a caught error to a Vietnamese message (each workspace's own mapper).
  formatError: (error: unknown) => string;
  // Performs the write and the parent refresh; must throw on failure.
  onSubmit: (rationale: string) => Promise<void>;
  // Called when the user chooses to reload after a 409 keeps their draft.
  onReload?: () => void;
}

// One append-only human gate-write form. The rationale (when shown) is never
// discarded on a 409 — the draft is kept and a reload is offered instead. There
// is no optimistic completion: the parent only refreshes after the server
// receipt returns.
export function RecordActionForm({
  submitLabel,
  pendingLabel = "Đang ghi vào sổ…",
  hint,
  showRationale = true,
  rationaleRequired = true,
  rationaleLabel = "Lý do",
  formatError,
  onSubmit,
  onReload,
}: RecordActionFormProps) {
  const baseId = useId();
  const rationaleId = `${baseId}-rationale`;
  const errorId = `${baseId}-error`;

  const [rationale, setRationale] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);

    const note = rationale.trim();
    if (showRationale && rationaleRequired && note.length === 0) {
      setFieldError("Nhập lý do trước khi ghi; đây là trường bắt buộc.");
      return;
    }
    setFieldError(null);

    setPending(true);
    try {
      await onSubmit(note);
      // Success: the parent refreshes and this form typically unmounts.
      setRationale("");
    } catch (requestError) {
      // House rule: a 409 keeps the draft and prompts a reload, never discards.
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(formatError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <p className={styles.formHint}>{hint}</p>

      {showRationale ? (
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={rationaleId}>
            {rationaleLabel}{" "}
            {rationaleRequired ? <span className={styles.required}>*</span> : null}
          </label>
          <textarea
            aria-describedby={fieldError || submitError ? errorId : undefined}
            aria-invalid={fieldError ? "true" : undefined}
            className={styles.textarea}
            disabled={pending}
            id={rationaleId}
            maxLength={MAX_RATIONALE}
            onChange={(event) => {
              setRationale(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            value={rationale}
          />
          <span className={styles.charCount}>
            {rationale.length}/{MAX_RATIONALE}
          </span>
        </div>
      ) : null}

      {fieldError ? (
        <p className={styles.fieldError} id={errorId} role="alert">
          {fieldError}
        </p>
      ) : null}

      {submitError ? (
        <div className={styles.submitError} id={errorId} role="alert">
          <p>{submitError}</p>
          {staleReload && onReload ? (
            <button
              className="button button-secondary"
              onClick={() => onReload()}
              type="button"
            >
              Tải lại
            </button>
          ) : null}
        </div>
      ) : null}

      <div className={styles.formActions}>
        <button aria-busy={pending} className={styles.submit} disabled={pending} type="submit">
          {pending ? pendingLabel : submitLabel}
        </button>
      </div>
    </form>
  );
}
