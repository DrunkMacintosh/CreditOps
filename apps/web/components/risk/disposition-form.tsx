"use client";

import React, { useId, useState } from "react";

import type { DispositionType, RecordDispositionInput } from "../../lib/api/risk-review";
import { DISPOSITION_TYPE_LABELS, getRiskReviewError } from "../../lib/api/risk-review";
import styles from "./risk-review.module.css";

const MAX_RATIONALE = 4000;

// The four disposition types a risk reviewer may record on a single challenge
// (services/.../api/risk_review.py::_DISPOSITION_TYPES). The officer RECORDS a
// disposition; nothing here decides credit.
const CHALLENGE_OPTIONS: readonly DispositionType[] = [
  "ACCEPTED_RISK",
  "MAKER_MUST_REVISE",
  "ESCALATED",
  "NOTED",
];

interface DispositionFormProps {
  // When set, the type is fixed (e.g. assessment-level NOTED) and no selector
  // is shown. Otherwise the reviewer picks one of the challenge options.
  fixedType?: DispositionType;
  heading: string;
  hint?: string;
  submitLabel: string;
  rationaleLabel: string;
  // Performs the write and any refresh; must throw on failure so the form can
  // surface a recovery message inline.
  onSubmit: (input: RecordDispositionInput) => Promise<void>;
}

export function DispositionForm({
  fixedType,
  heading,
  hint,
  submitLabel,
  rationaleLabel,
  onSubmit,
}: DispositionFormProps) {
  const baseId = useId();
  const rationaleId = `${baseId}-rationale`;
  const errorId = `${baseId}-error`;
  const legendId = `${baseId}-legend`;

  const [type, setType] = useState<DispositionType | null>(fixedType ?? null);
  const [rationale, setRationale] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);

    const chosen = fixedType ?? type;
    if (!chosen) {
      setFieldError("Chọn một loại quyết định trước khi ghi.");
      return;
    }
    const note = rationale.trim();
    if (note.length === 0) {
      setFieldError("Nhập lý do cho quyết định; đây là trường bắt buộc.");
      return;
    }
    setFieldError(null);

    setPending(true);
    try {
      await onSubmit({ dispositionType: chosen, rationale: note });
      // Success: the parent refreshes and this form unmounts. Reset defensively.
      setRationale("");
      if (!fixedType) setType(null);
    } catch (requestError) {
      setSubmitError(getRiskReviewError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <p className={styles.formHeading}>{heading}</p>
      {hint ? <p className={styles.formHint}>{hint}</p> : null}

      {fixedType ? (
        <p className={styles.fixedType}>
          Loại quyết định: <strong>{DISPOSITION_TYPE_LABELS[fixedType]}</strong>
        </p>
      ) : (
        <fieldset aria-labelledby={legendId} className={styles.fieldset}>
          <legend className={styles.fieldsetLegend} id={legendId}>
            Loại quyết định <span className={styles.required}>*</span>
          </legend>
          <div className={styles.radioGroup}>
            {CHALLENGE_OPTIONS.map((option) => (
              <label
                className={styles.radioOption}
                data-checked={type === option ? "true" : "false"}
                key={option}
              >
                <input
                  checked={type === option}
                  disabled={pending}
                  name={`${baseId}-type`}
                  onChange={() => {
                    setType(option);
                    setFieldError(null);
                  }}
                  type="radio"
                  value={option}
                />
                <span>{DISPOSITION_TYPE_LABELS[option]}</span>
              </label>
            ))}
          </div>
        </fieldset>
      )}

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
        <button aria-busy={pending} className={styles.submit} disabled={pending} type="submit">
          {pending ? "Đang ghi quyết định…" : submitLabel}
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
