import React from "react";

// Gate vocabulary shared across orchestration, underwriting, legal and risk
// screens. Language is deliberately about evidence gates — never approval.
export type GateStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "PASSED"
  | "FAILED"
  | "BLOCKED"
  | "SUPERSEDED";

type ChipVariant = "ok" | "amber" | "risk" | "info" | "muted";

export const STATUS_LABELS: Record<GateStatus, string> = {
  PENDING: "Đang chờ",
  RUNNING: "Đang xử lý",
  SUCCEEDED: "Đạt",
  PASSED: "Đạt",
  FAILED: "Chưa đạt",
  BLOCKED: "Chưa đạt",
  SUPERSEDED: "Đã thay thế",
};

const STATUS_VARIANTS: Record<GateStatus, ChipVariant> = {
  PENDING: "amber",
  RUNNING: "info",
  SUCCEEDED: "ok",
  PASSED: "ok",
  FAILED: "risk",
  BLOCKED: "risk",
  SUPERSEDED: "muted",
};

export function statusLabel(status: GateStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function StatusChip({
  status,
  label,
  className,
}: {
  status: GateStatus;
  /** Overrides the default Vietnamese label when a screen needs more context. */
  label?: string;
  className?: string;
}) {
  const variant = STATUS_VARIANTS[status] ?? "muted";
  const classes = ["status-chip", `status-chip--${variant}`, className]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={classes} data-status={status}>
      {label ?? STATUS_LABELS[status] ?? status}
    </span>
  );
}
