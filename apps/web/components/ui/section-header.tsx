import React, { type ReactNode } from "react";

// Eyebrow + title + optional trailing actions. Used as the standard heading
// block for sections and as the head of a DataCard.
export function SectionHeader({
  title,
  eyebrow,
  actions,
  id,
}: {
  title: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  id?: string;
}) {
  return (
    <header className="section-header">
      <div>
        {eyebrow ? <p className="section-eyebrow">{eyebrow}</p> : null}
        <h2 className="section-header-title" id={id}>
          {title}
        </h2>
      </div>
      {actions ? <div className="section-header-actions">{actions}</div> : null}
    </header>
  );
}
