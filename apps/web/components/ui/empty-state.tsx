import React, { type ReactNode } from "react";

// Empty states invite the next action rather than reporting a dead end.
export function EmptyState({
  title,
  hint,
  action,
  className,
}: {
  title: ReactNode;
  hint: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const classes = ["empty-state", className].filter(Boolean).join(" ");
  return (
    <div className={classes}>
      <p className="empty-state-title">{title}</p>
      <p className="empty-state-hint">{hint}</p>
      {action ? <div className="empty-state-action">{action}</div> : null}
    </div>
  );
}
