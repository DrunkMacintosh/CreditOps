// Vietnamese labels and formatters for the underwriting working-paper. Kept
// separate from the view so the label vocabulary (which mirrors the backend's
// synthetic field-key taxonomy in application/underwriting/evidence.py) is easy
// to audit. We never invent a translation for an unknown key — we fall back to
// the raw name so nothing is silently misrepresented.

import type { Citation, FactRef } from "../../lib/api/underwriting";

export type MetricUnit = "ratio" | "percent" | "days" | "currency";

interface MetricMeta {
  readonly label: string;
  readonly unit: MetricUnit;
}

// Reported financial figures (đồng), keyed by calculator input name.
export const FINANCIAL_FIGURE_LABELS: Record<string, string> = {
  current_assets: "Tài sản ngắn hạn",
  current_liabilities: "Nợ ngắn hạn",
  inventory: "Hàng tồn kho",
  total_debt: "Tổng nợ vay",
  total_equity: "Vốn chủ sở hữu",
  total_assets: "Tổng tài sản",
  revenue: "Doanh thu",
  gross_profit: "Lợi nhuận gộp",
  operating_profit: "Lợi nhuận thuần từ hoạt động kinh doanh",
  net_profit: "Lợi nhuận sau thuế",
  accounts_receivable: "Khoản phải thu",
  accounts_payable: "Khoản phải trả",
  cost_of_goods_sold: "Giá vốn hàng bán",
  own_working_capital: "Vốn lưu động tự có",
  other_funding_sources: "Nguồn tài trợ khác",
};

// Deterministic-calculator output metadata, keyed by `calculator` name.
export const METRIC_META: Record<string, MetricMeta> = {
  current_ratio: { label: "Hệ số thanh toán hiện hành", unit: "ratio" },
  quick_ratio: { label: "Hệ số thanh toán nhanh", unit: "ratio" },
  debt_to_equity: { label: "Nợ trên vốn chủ sở hữu", unit: "ratio" },
  debt_to_assets: { label: "Nợ trên tổng tài sản", unit: "ratio" },
  gross_margin: { label: "Biên lợi nhuận gộp", unit: "percent" },
  operating_margin: { label: "Biên lợi nhuận hoạt động", unit: "percent" },
  net_margin: { label: "Biên lợi nhuận ròng", unit: "percent" },
  return_on_assets: { label: "Tỷ suất sinh lời trên tài sản (ROA)", unit: "percent" },
  return_on_equity: { label: "Tỷ suất sinh lời trên vốn chủ (ROE)", unit: "percent" },
  receivable_days: { label: "Số ngày phải thu bình quân", unit: "days" },
  inventory_days: { label: "Số ngày tồn kho bình quân", unit: "days" },
  payable_days: { label: "Số ngày phải trả bình quân", unit: "days" },
  asset_turnover: { label: "Vòng quay tổng tài sản", unit: "ratio" },
  cash_conversion_cycle: { label: "Chu kỳ chuyển đổi tiền mặt", unit: "days" },
  working_capital_need: { label: "Nhu cầu vốn lưu động", unit: "currency" },
  working_capital_gap: { label: "Thiếu hụt vốn lưu động", unit: "currency" },
};

const METRIC_LABELS: Record<string, string> = {
  revenue: "Doanh thu",
  net_profit: "Lợi nhuận sau thuế",
  operating_profit: "Lợi nhuận thuần từ hoạt động kinh doanh",
};

const PERIOD_LABELS: Record<string, string> = {
  ky_truoc: "Kỳ trước",
  ky_hien_tai: "Kỳ hiện tại",
};

const SCENARIO_LABELS: Record<string, string> = {
  doanh_thu_giam_20pct: "Kịch bản doanh thu giảm 20%",
};

export function figureLabel(name: string): string {
  return FINANCIAL_FIGURE_LABELS[name] ?? name;
}

export function metricMeta(calculator: string): MetricMeta {
  return METRIC_META[calculator] ?? { label: calculator, unit: "ratio" };
}

export function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? FINANCIAL_FIGURE_LABELS[metric] ?? metric;
}

export function periodLabel(period: string): string {
  return PERIOD_LABELS[period] ?? period;
}

export function scenarioLabel(name: string): string {
  return SCENARIO_LABELS[name] ?? name;
}

const vnd = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 });
const decimal2 = new Intl.NumberFormat("vi-VN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const decimal1 = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 });

export const NO_VALUE = "—";

export function formatCurrency(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return NO_VALUE;
  return `${vnd.format(Math.round(value))} đ`;
}

export function formatByUnit(value: number | null, unit: MetricUnit): string {
  if (value === null || !Number.isFinite(value)) return NO_VALUE;
  switch (unit) {
    case "currency":
      return formatCurrency(value);
    case "percent":
      return `${decimal1.format(value * 100)}%`;
    case "days":
      return `${decimal1.format(value)} ngày`;
    case "ratio":
    default:
      return `${decimal2.format(value)} lần`;
  }
}

export function formatGrowth(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return NO_VALUE;
  const percent = value * 100;
  const sign = percent > 0 ? "+" : "";
  return `${sign}${decimal1.format(percent)}%`;
}

export function formatDateTime(iso: string): string {
  if (!iso) return NO_VALUE;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}

// Shorten a UUID/opaque reference for the mono evidence chip while keeping the
// full value available in a title attribute.
export function shortRef(ref: string): string {
  if (!ref) return NO_VALUE;
  return ref.length > 8 ? `${ref.slice(0, 8)}…` : ref;
}

export interface EvidenceChipModel {
  readonly key: string;
  readonly kindLabel: string;
  readonly ref: string;
  readonly title: string;
}

export function factRefChip(ref: FactRef): EvidenceChipModel {
  const kindLabel = ref.kind === "DOCUMENT_REGION" ? "Tài liệu" : "Dữ kiện đã xác nhận";
  return {
    key: `${ref.kind}:${ref.refId}`,
    kindLabel,
    ref: shortRef(ref.refId),
    title: `${kindLabel} · ${ref.refId}`,
  };
}

// Deduplicate a set of fact references (e.g. the union behind one ratio) so the
// same confirmed fact never renders twice on a row.
export function dedupeFactRefs(refs: readonly FactRef[]): FactRef[] {
  const seen = new Map<string, FactRef>();
  for (const ref of refs) {
    seen.set(`${ref.kind}:${ref.refId}`, ref);
  }
  return [...seen.values()];
}

export function citationChip(
  citation: Citation,
  resultLabel: (resultId: string) => string,
): EvidenceChipModel {
  if (citation.kind === "CALCULATOR_RESULT") {
    const id = citation.resultId ?? "";
    return {
      key: `CALC:${id}`,
      kindLabel: "Chỉ số đã tính",
      ref: resultLabel(id),
      title: `Kết quả tính toán · ${id}`,
    };
  }
  if (citation.kind === "DOCUMENT_REGION") {
    const doc = citation.documentVersionId ?? "";
    const region = citation.region ?? "";
    return {
      key: `DOC:${doc}:${region}`,
      kindLabel: "Tài liệu",
      ref: region ? `${shortRef(doc)} · ${region}` : shortRef(doc),
      title: `Vùng tài liệu · ${doc}${region ? ` · ${region}` : ""}`,
    };
  }
  const id = citation.confirmedFactId ?? "";
  return {
    key: `FACT:${id}`,
    kindLabel: "Dữ kiện đã xác nhận",
    ref: shortRef(id),
    title: `Dữ kiện đã xác nhận · ${id}`,
  };
}

export const CONFIDENCE_LABELS: Record<string, string> = {
  HIGH: "Độ tin cậy cao",
  MEDIUM: "Độ tin cậy trung bình",
  LOW: "Độ tin cậy thấp",
};

export const GAP_LEVEL_LABELS: Record<string, string> = {
  BLOCKING: "Chặn",
  CONDITIONAL: "Có điều kiện",
  CLARIFICATION: "Cần làm rõ",
};

export const HANDOFF_STATE_LABELS: Record<string, string> = {
  READY_FOR_RISK_REVIEW: "Sẵn sàng chuyển thẩm định rủi ro",
};

export function handoffStateLabel(state: string): string {
  return HANDOFF_STATE_LABELS[state] ?? state;
}
