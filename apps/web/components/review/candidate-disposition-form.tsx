"use client";

import React from "react";

import type { CandidateFactDto, FactDisposition } from "../../lib/api/contracts";
import { fieldLabelVi } from "../../lib/review/field-labels";
import styles from "./document-review.module.css";

export interface CandidateDraft {
  disposition: FactDisposition | null;
  correctedValue: string;
  rationale: string;
}

// Accessible names are EXACTLY "{label} {field}" so screen-reader users and the
// review tests resolve each radio unambiguously.
const DISPOSITION_OPTIONS: readonly { value: FactDisposition; label: string }[] = [
  { value: "ACCEPTED", label: "Chấp nhận" },
  { value: "CORRECTED", label: "Chỉnh sửa" },
  { value: "ABSENT", label: "Không có trong tài liệu" },
  { value: "UNREADABLE", label: "Không đọc được" },
];

interface CandidateDispositionFormProps {
  candidate: CandidateFactDto;
  draft: CandidateDraft;
  onChange: (patch: Partial<CandidateDraft>) => void;
  onSelect: () => void;
  selected: boolean;
  disabled: boolean;
  fieldsetRef?: (element: HTMLFieldSetElement | null) => void;
}

export function CandidateDispositionForm({
  candidate,
  draft,
  onChange,
  onSelect,
  selected,
  disabled,
  fieldsetRef,
}: CandidateDispositionFormProps) {
  const label = fieldLabelVi(candidate.fieldKey);
  const source = candidate.source;
  const correctedValueId = `${candidate.id}-corrected-value`;
  const rationaleId = `${candidate.id}-rationale`;

  return (
    <fieldset
      className={styles.candidate}
      data-selected={selected ? "true" : "false"}
      onClick={onSelect}
      onFocus={onSelect}
      ref={fieldsetRef}
      tabIndex={-1}
    >
      <legend className={styles.candidateLegend}>
        <span className={styles.fieldName}>{label}</span>
      </legend>

      <dl className={styles.candidateFacts}>
        <div>
          <dt>Giá trị đề xuất</dt>
          <dd>{formatProposedValue(candidate.proposedValue)}</dd>
        </div>
        <div>
          <dt>Độ tin cậy</dt>
          <dd>{Math.round(candidate.confidence * 100)}%</dd>
        </div>
        <div>
          <dt>Vị trí nguồn</dt>
          <dd>
            Trang {source.page} · x {formatPercent(source.x)}, y{" "}
            {formatPercent(source.y)}, rộng {formatPercent(source.width)}, cao{" "}
            {formatPercent(source.height)}
          </dd>
        </div>
      </dl>

      <div>
        <button
          className="button button-secondary button-small"
          onClick={onSelect}
          type="button"
        >
          Xem vùng nguồn
        </button>
      </div>

      <div className={styles.options}>
        {DISPOSITION_OPTIONS.map((option) => (
          <label className={styles.option} key={option.value}>
            <input
              aria-label={`${option.label} ${label}`}
              checked={draft.disposition === option.value}
              disabled={disabled}
              name={candidate.id}
              onChange={() => onChange({ disposition: option.value })}
              type="radio"
              value={option.value}
            />
            <span aria-hidden="true">{option.label}</span>
          </label>
        ))}
      </div>

      {draft.disposition === "CORRECTED" ? (
        <div className={styles.correction}>
          <div className={styles.field}>
            <label htmlFor={correctedValueId}>Giá trị đã chỉnh sửa</label>
            <input
              disabled={disabled}
              id={correctedValueId}
              onChange={(event) => onChange({ correctedValue: event.target.value })}
              type="text"
              value={draft.correctedValue}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor={rationaleId}>Lý do chỉnh sửa</label>
            <textarea
              disabled={disabled}
              id={rationaleId}
              onChange={(event) => onChange({ rationale: event.target.value })}
              value={draft.rationale}
            />
          </div>
        </div>
      ) : null}
    </fieldset>
  );
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatProposedValue(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "Có" : "Không";
  if (typeof value === "number") return new Intl.NumberFormat("vi-VN").format(value);
  return value;
}
