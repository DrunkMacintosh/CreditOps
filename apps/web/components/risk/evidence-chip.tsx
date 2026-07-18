import React from "react";

import type { EvidenceCitation, MakerFindingRef } from "../../lib/api/risk-review";
import { MAKER_SOURCE_LABELS, labelFor } from "../../lib/api/risk-review";
import styles from "./risk-review.module.css";

// The "chứng cứ" chip: a small, consistent provenance marker naming the source
// (kind + mono reference), used identically everywhere a claim is grounded.
// Leaf-green dot, 1px line, mono reference, tabular numerals.
export function EvidenceChip({
  kind,
  reference,
  title,
  variant = "citation",
}: {
  kind: string;
  reference: string;
  title?: string;
  variant?: "citation" | "target";
}) {
  return (
    <span
      className={`${styles.evidenceChip} ${
        variant === "target" ? styles.evidenceChipTarget : ""
      }`}
      title={title}
    >
      <span aria-hidden="true" className={styles.chipDot} />
      <span className={styles.chipText}>
        <span className={styles.chipKind}>{kind}</span>
        <span className={styles.chipRef}>{reference}</span>
      </span>
    </span>
  );
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value || "—";
}

function makerLabel(source: string): string {
  return labelFor(MAKER_SOURCE_LABELS, source);
}

// A challenge target points at the exact maker passage being disputed.
export function targetChip(ref: MakerFindingRef): {
  kind: string;
  reference: string;
  title: string;
} {
  return {
    kind: makerLabel(ref.makerSource),
    reference: ref.sectionPath || "—",
    title: `Đối tượng bị thách thức · ${makerLabel(ref.makerSource)} · ${ref.sectionPath}`,
  };
}

// Maps one evidence citation to its chip fields. Each citation kind resolves to
// a human label plus a monospace reference the reviewer can trace.
export function citationChip(citation: EvidenceCitation): {
  kind: string;
  reference: string;
  title: string;
} {
  switch (citation.kind) {
    case "CONFIRMED_FACT":
      return {
        kind: "Dữ kiện đã xác nhận",
        reference: shortId(citation.confirmedFactId),
        title: `Dữ kiện đã xác nhận · ${citation.confirmedFactId}`,
      };
    case "CALCULATOR_RESULT":
      return {
        kind: "Kết quả tính toán",
        reference: citation.resultId || "—",
        title: `Kết quả tính toán · ${citation.resultId}`,
      };
    case "DOCUMENT_REGION":
      return {
        kind: "Vùng tài liệu",
        reference: citation.region || shortId(citation.documentVersionId),
        title: `Vùng tài liệu · ${citation.documentVersionId} · ${citation.region}`,
      };
    case "POLICY_CITATION":
      return {
        kind: "Trích dẫn chính sách",
        reference: `${citation.documentId} · ${citation.clauseId}`,
        title: `Chính sách ${citation.corpusId}@${citation.corpusVersion} · ${citation.documentId} · điều ${citation.clauseId}\n${citation.quotedTextVi}`,
      };
    case "CONTROLLED_CHECK":
      return {
        kind: "Kiểm tra có kiểm soát",
        reference: shortId(citation.invocationId),
        title: `Kiểm tra có kiểm soát · ${citation.invocationId}`,
      };
    case "MAKER_FINDING":
      return {
        kind: makerLabel(citation.ref.makerSource),
        reference: citation.ref.sectionPath || "—",
        title: `Kết luận của bên lập · ${makerLabel(citation.ref.makerSource)} · ${citation.ref.sectionPath}`,
      };
    default:
      return { kind: "Chứng cứ", reference: citation.label || "—", title: "Chứng cứ" };
  }
}
