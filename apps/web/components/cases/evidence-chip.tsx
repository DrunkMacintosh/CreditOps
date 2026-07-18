import React from "react";

import styles from "./evidence-chip.module.css";

// The evidence-chain chip: the product's signature element. It names a source
// (a dossier, a document) and shows its stable reference in mono type, so every
// material record on screen carries its provenance. Kept visually identical
// everywhere it appears.
export function EvidenceChip({
  label,
  reference,
  title,
}: {
  label: string;
  reference: string;
  title?: string;
}) {
  return (
    <span className={styles.chip} title={title}>
      <span aria-hidden="true" className={styles.dot} />
      <span className={styles.label}>{label}</span>
      <span className={styles.reference}>{reference}</span>
    </span>
  );
}

// Trims an opaque identifier to a compact, readable reference without losing its
// recognizable prefix. Never fabricates — an empty id renders as a dash.
export function shortReference(value: string): string {
  if (!value) return "—";
  return value.length > 16 ? `${value.slice(0, 12)}…` : value;
}
