"use client";

import React, { useId, useState } from "react";

import { getCreditOpsError } from "../../lib/api/credit-ops";
import styles from "./credit-ops.module.css";

const MAX_RATIONALE = 4000;

interface AuthorizationFormProps {
  // What the button does, in plain terms (e.g. "Ghi ủy quyền hành động").
  submitLabel: string;
  // Label for the required rationale field.
  rationaleLabel: string;
  // Short instruction shown above the field, naming exactly what recording does.
  hint: string;
  // Performs the write and the parent refresh; must throw on failure so the
  // form can surface a recovery message inline.
  onSubmit: (input: { rationale: string }) => Promise<void>;
}

// One rationale-capturing record form, shared by the G4 action-authorization
// and G2 document-request-approval surfaces. The officer RECORDS authority;
// nothing here executes an action or sends a request.
export function AuthorizationForm({
  submitLabel,
  rationaleLabel,
  hint,
  onSubmit,
}: AuthorizationFormProps) {
  const baseId = useId();
  const rationaleId = `${baseId}-rationale`;
  const errorId = `${baseId}-error`;

  const [rationale, setRationale] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);

    const note = rationale.trim();
    if (note.length === 0) {
      setFieldError("Nhập lý do trước khi ghi; đây là trường bắt buộc.");
      return;
    }
    setFieldError(null);

    setPending(true);
    try {
      await onSubmit({ rationale: note });
      // Success: the parent refreshes and this form unmounts. Reset defensively.
      setRationale("");
    } catch (requestError) {
      setSubmitError(getCreditOpsError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <p className={styles.formHint}>{hint}</p>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={rationaleId}>
          {rationaleLabel} <span className={styles.required}>*</span>
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

      {(fieldError || submitError) && (
        <p className={styles.fieldError} id={errorId} role="alert">
          {fieldError ?? submitError}
        </p>
      )}

      <div className={styles.formActions}>
        <button
          aria-busy={pending}
          className={styles.submit}
          disabled={pending}
          type="submit"
        >
          {pending ? "Đang ghi vào sổ…" : submitLabel}
        </button>
        {pending ? (
          <span aria-live="polite" className={styles.pendingNote}>
            Đã tiếp nhận yêu cầu; đang ghi vào sổ.
          </span>
        ) : null}
      </div>
    </form>
  );
}
