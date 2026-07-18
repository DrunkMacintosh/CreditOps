import Link from "next/link";
import React from "react";

// The product's signature marker: every material number or claim names the
// source document and its version. Renders as a static note, or as a link to
// the underlying document when `href` is provided.
export function EvidenceChip({
  documentName,
  versionLabel,
  href,
  className,
}: {
  documentName: string;
  versionLabel: string;
  href?: string;
  className?: string;
}) {
  const classes = ["evidence-chip", className].filter(Boolean).join(" ");
  const ariaLabel = `Chứng cứ: ${documentName}, ${versionLabel}`;
  const inner = (
    <>
      <span aria-hidden="true" className="evidence-chip-dot" />
      <span className="evidence-chip-label">Chứng cứ</span>
      <span className="evidence-chip-doc" title={documentName}>
        {documentName}
      </span>
      <span className="evidence-chip-ver">{versionLabel}</span>
    </>
  );

  if (href) {
    return (
      <Link aria-label={ariaLabel} className={classes} href={href}>
        {inner}
      </Link>
    );
  }

  return (
    <span aria-label={ariaLabel} className={classes} role="note">
      {inner}
    </span>
  );
}
