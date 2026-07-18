import React from "react";

import type { EvidenceCitation, LegalConfidence, LegalFinding } from "../../lib/api/legal";
import styles from "./legal.module.css";

// The "chứng cứ" (evidence) chip is the product's signature: every material
// claim names its source. One consistent shape everywhere — leaf-green dot,
// 1px line, mono reference. It never asserts a conclusion, only points to the
// document/fact/clause/check the statement rests on.

export type GateTone = "ok" | "risk" | "amber" | "info" | "muted";

const GATE_CLASS: Record<GateTone, string> = {
  ok: styles.gateOk,
  risk: styles.gateRisk,
  amber: styles.gateAmber,
  info: styles.gateInfo,
  muted: styles.gateMuted,
};

export function GateChip({ label, tone }: { label: string; tone: GateTone }) {
  return <span className={`${styles.gate} ${GATE_CLASS[tone]}`}>{label}</span>;
}

const CONFIDENCE_LABEL: Record<LegalConfidence, string> = {
  HIGH: "Độ tin cậy cao",
  MEDIUM: "Độ tin cậy trung bình",
  LOW: "Độ tin cậy thấp",
};

const CONFIDENCE_CLASS: Record<LegalConfidence, string> = {
  HIGH: styles.confHigh,
  MEDIUM: styles.confMedium,
  LOW: styles.confLow,
};

export function ConfidenceNote({ confidence }: { confidence: LegalConfidence | null }) {
  if (confidence === null) return null;
  return (
    <span className={`${styles.confidence} ${CONFIDENCE_CLASS[confidence]}`}>
      {CONFIDENCE_LABEL[confidence]}
    </span>
  );
}

function shortId(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length === 0) return "—";
  return `#${trimmed.slice(0, 8)}`;
}

interface ChipContent {
  kind: string;
  ref: string;
  quote?: string;
  aria: string;
}

function citationContent(citation: EvidenceCitation): ChipContent {
  switch (citation.kind) {
    case "CONFIRMED_FACT":
      return {
        kind: "chứng cứ · dữ kiện",
        ref: shortId(citation.confirmedFactId),
        aria: `Chứng cứ: dữ kiện đã xác nhận ${citation.confirmedFactId}`,
      };
    case "DOCUMENT_REGION":
      return {
        kind: "chứng cứ · tài liệu",
        ref: shortId(citation.documentVersionId),
        quote: citation.region,
        aria: `Chứng cứ: tài liệu ${citation.documentVersionId}, vùng ${citation.region}`,
      };
    case "POLICY_CITATION":
      return {
        kind: "chứng cứ · chính sách",
        ref: `${citation.documentId}/${citation.clauseId} · ${citation.corpusVersion}`,
        quote: citation.quotedTextVi,
        aria: `Chứng cứ: điều khoản chính sách ${citation.documentId}/${citation.clauseId}, phiên bản ${citation.corpusVersion}`,
      };
    case "CONTROLLED_CHECK":
      return {
        kind: "chứng cứ · kiểm tra",
        ref: shortId(citation.invocationId),
        aria: `Chứng cứ: kết quả kiểm tra kiểm soát ${citation.invocationId}`,
      };
    default:
      return {
        kind: "chứng cứ",
        ref: citation.label,
        aria: `Chứng cứ: ${citation.label}`,
      };
  }
}

export function EvidenceChip({ citation }: { citation: EvidenceCitation }) {
  const content = citationContent(citation);
  return (
    <span className={styles.chip} aria-label={content.aria} title={content.aria}>
      <span aria-hidden="true" className={styles.chipDot} />
      <span className={styles.chipKind}>{content.kind}</span>
      <span className={styles.chipRef}>{content.ref}</span>
      {content.quote ? (
        <span className={styles.chipQuote} title={content.quote}>
          “{content.quote}”
        </span>
      ) : null}
    </span>
  );
}

export function EvidenceChips({ citations }: { citations: EvidenceCitation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className={styles.chips}>
      {citations.map((citation, index) => (
        <EvidenceChip citation={citation} key={`${citation.kind}-${index}`} />
      ))}
    </div>
  );
}

// A single cited analytical statement. Uncertainty is surfaced, never hidden —
// the officer sees exactly what the assessment is unsure about.
export function FindingItem({ finding }: { finding: LegalFinding }) {
  return (
    <li className={styles.finding}>
      <p className={styles.body}>{finding.statementVi}</p>
      <EvidenceChips citations={finding.citations} />
      <div className={styles.metaRow}>
        <ConfidenceNote confidence={finding.confidence} />
      </div>
      {finding.uncertaintyVi ? (
        <p className={styles.uncertainty}>
          <b>Điểm chưa chắc chắn:</b> {finding.uncertaintyVi}
        </p>
      ) : null}
    </li>
  );
}

export function FindingList({
  findings,
  emptyLabel,
}: {
  findings: LegalFinding[];
  emptyLabel: string;
}) {
  if (findings.length === 0) {
    return <p className={styles.empty}>{emptyLabel}</p>;
  }
  return (
    <ul className={styles.findingList}>
      {findings.map((finding, index) => (
        <FindingItem finding={finding} key={index} />
      ))}
    </ul>
  );
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("vi-VN");
}

export { styles as legalStyles };
