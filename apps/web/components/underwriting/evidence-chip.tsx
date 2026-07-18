import React from "react";

import type { EvidenceChipModel } from "./format";
import styles from "./evidence-chip.module.css";

// The product's signature element: a "chứng cứ" chip that names the source of a
// number or claim. Rendered identically everywhere — 1px line, small radius,
// mono reference text, a leaf-green source dot. It states provenance only; it
// never asserts a decision.
export function EvidenceChip({ chip }: { chip: EvidenceChipModel }) {
  return (
    <span className={styles.chip} title={chip.title}>
      <span aria-hidden="true" className={styles.dot} />
      <span className={styles.kind}>{chip.kindLabel}</span>
      <span className={styles.ref}>{chip.ref}</span>
    </span>
  );
}

export function EvidenceChipList({
  chips,
  label = "Chứng cứ",
}: {
  chips: readonly EvidenceChipModel[];
  label?: string;
}) {
  if (chips.length === 0) {
    return <span className={styles.none}>Không có tham chiếu chứng cứ</span>;
  }
  return (
    <span className={styles.list} aria-label={label}>
      {chips.map((chip) => (
        <EvidenceChip chip={chip} key={chip.key} />
      ))}
    </span>
  );
}
