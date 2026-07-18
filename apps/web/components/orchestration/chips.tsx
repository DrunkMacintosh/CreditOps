import React from "react";

import type { ChipTone } from "./labels";
import styles from "./orchestration.module.css";

const TONE_CLASS: Record<ChipTone, string> = {
  ok: styles.toneOk,
  amber: styles.toneAmber,
  info: styles.toneInfo,
  risk: styles.toneRisk,
  muted: styles.toneMuted,
};

// A gate-language status chip: soft background + strong text from the token
// pairs. `pulse` adds the one subtle running indicator (respects reduced
// motion via CSS).
export function StatusChip({
  label,
  tone,
  pulse = false,
}: {
  label: string;
  tone: ChipTone;
  pulse?: boolean;
}) {
  return (
    <span className={`${styles.statusChip} ${TONE_CLASS[tone]}`}>
      {pulse ? <span aria-hidden="true" className={styles.pulseDot} /> : null}
      {label}
    </span>
  );
}

// The product's signature element: an evidence ("chứng cứ") chip that names the
// provenance of a claim. Leaf-green dot + mono reference text, 1px line border.
// `detail` carries an optional secondary reference (e.g. a timestamp).
export function EvidenceChip({
  reference,
  detail,
  label,
}: {
  reference: string;
  detail?: string;
  label?: string;
}) {
  return (
    <span className={styles.evidenceChip} aria-label={label}>
      <span aria-hidden="true" className={styles.evidenceDot} />
      <span className={styles.evidenceRef}>{reference}</span>
      {detail ? <span className={styles.evidenceDetail}>{detail}</span> : null}
    </span>
  );
}
