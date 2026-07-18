import React, { type ReactNode } from "react";

import { SectionHeader } from "./section-header";

// A white data surface on the cream canvas. The header block is optional; a
// card with only children renders as a plain padded panel.
export function DataCard({
  title,
  eyebrow,
  actions,
  children,
  className,
  headingId,
}: {
  title?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  headingId?: string;
}) {
  const classes = ["data-card", className].filter(Boolean).join(" ");
  return (
    <section className={classes}>
      {title ? (
        <SectionHeader actions={actions} eyebrow={eyebrow} id={headingId} title={title} />
      ) : null}
      {children}
    </section>
  );
}
