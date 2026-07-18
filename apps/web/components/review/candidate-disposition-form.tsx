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
  // 1-based marker that ties this field to its numbered region in the viewer.
  index: number;
  // Provenance shown in the "chứng cứ" chip: the source document + its version.
  documentLabel: string | null;
  documentVersion: number;
  fieldsetRef?: (element: HTMLFieldSetElement | null) => void;
}

export function CandidateDispositionForm({
  candidate,
  draft,
  onChange,
  onSelect,
  selected,
  disabled,
  index,
  documentLabel,
  documentVersion,
  fieldsetRef,
}: CandidateDispositionFormProps) {
  const label = fieldLabelVi(candidate.fieldKey);
  const source = candidate.source;
  const correctedValueId = `${candidate.id}-corrected-value`;
  const correctedHelpId = `${candidate.id}-corrected-help`;
  const rationaleId = `${candidate.id}-rationale`;
  const rationaleHelpId = `${candidate.id}-rationale-help`;
  const confidencePct = Math.round(candidate.confidence * 100);
  const confidenceLevel = confidenceBand(candidate.confidence);
  const documentRef = documentLabel && documentLabel.trim().length > 0
    ? documentLabel
    : "Tài liệu";

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
        <span className={styles.legendMain}>
          <span aria-hidden="true" className={styles.fieldIndex}>
            {index}
          </span>
          <span className={styles.fieldName}>{label}</span>
        </span>
        <span
          className={styles.confChip}
          data-level={confidenceLevel}
          title={`Độ tin cậy trích xuất ${confidencePct}%`}
        >
          <span className={styles.confPct}>{confidencePct}%</span>
          <span className={styles.confCap}>tin cậy</span>
        </span>
      </legend>

      <div className={styles.valueBlock}>
        <span className={styles.valueLabel}>Giá trị đề xuất</span>
        <span className={styles.proposedValue}>
          {formatProposedValue(candidate.proposedValue)}
        </span>
      </div>

      <div className={styles.evidenceRow}>
        <span className={styles.evidenceChip}>
          <span aria-hidden="true" className={styles.evidenceDot} />
          <span className={styles.evidenceLabel}>Chứng cứ</span>
          <span className={styles.evidenceRef}>
            {documentRef} · v{documentVersion} · tr.{source.page}
          </span>
        </span>
        <button
          className={`button button-secondary button-small ${styles.viewSource}`}
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
              aria-describedby={correctedHelpId}
              disabled={disabled}
              id={correctedValueId}
              onChange={(event) => onChange({ correctedValue: event.target.value })}
              type="text"
              value={draft.correctedValue}
            />
            <p className={styles.fieldHelp} id={correctedHelpId}>
              Nhập giá trị đúng theo tài liệu. Bắt buộc để xác nhận.
            </p>
          </div>
          <div className={styles.field}>
            <label htmlFor={rationaleId}>Lý do chỉnh sửa</label>
            <textarea
              aria-describedby={rationaleHelpId}
              disabled={disabled}
              id={rationaleId}
              onChange={(event) => onChange({ rationale: event.target.value })}
              value={draft.rationale}
            />
            <p className={styles.fieldHelp} id={rationaleHelpId}>
              Nêu căn cứ cho chỉnh sửa để lưu vào nhật ký. Bắt buộc để xác nhận.
            </p>
          </div>
        </div>
      ) : null}
    </fieldset>
  );
}

function confidenceBand(confidence: number): "high" | "medium" | "low" {
  if (confidence >= 0.85) return "high";
  if (confidence >= 0.6) return "medium";
  return "low";
}

function formatProposedValue(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "Có" : "Không";
  if (typeof value === "number") return new Intl.NumberFormat("vi-VN").format(value);
  return value;
}
