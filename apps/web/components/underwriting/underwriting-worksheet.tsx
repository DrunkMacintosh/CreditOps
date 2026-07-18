"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  fetchUnderwritingAssessment,
  UNDERWRITING_NOT_AVAILABLE,
  type Assessment,
  type CalculatorOutcome,
  type CalculatorResult,
  type Citation,
  type EvidenceGapItem,
  type Finding,
  type RiskItem,
  type ScenarioResult,
  type TrendResult,
  type UnderwritingAssessmentView,
} from "../../lib/api/underwriting";
import { CaseNav } from "../shell/case-nav";
import { EvidenceChipList } from "./evidence-chip";
import {
  CONFIDENCE_LABELS,
  GAP_LEVEL_LABELS,
  citationChip,
  dedupeFactRefs,
  factRefChip,
  figureLabel,
  formatByUnit,
  formatCurrency,
  formatDateTime,
  formatGrowth,
  handoffStateLabel,
  metricLabel,
  metricMeta,
  periodLabel,
  scenarioLabel,
  FINANCIAL_FIGURE_LABELS,
  NO_VALUE,
  type EvidenceChipModel,
} from "./format";
import styles from "./underwriting-worksheet.module.css";

type Tone = "ok" | "amber" | "info" | "risk" | "muted";

function Chip({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return <span className={`${styles.chip} ${styles[tone]}`}>{children}</span>;
}

// Build a stable resultId -> human label map so calculator-result citations
// name the metric they came from instead of an opaque hash.
function useResultLabels(assessment: Assessment): (resultId: string) => string {
  return useMemo(() => {
    const labels = new Map<string, string>();
    for (const result of assessment.calculatorResults) {
      labels.set(result.resultId, metricMeta(result.calculator).label);
    }
    for (const trend of assessment.trendResults) {
      labels.set(trend.resultId, `Xu hướng ${metricLabel(trend.metric)}`);
    }
    for (const scenario of assessment.scenarioResults) {
      labels.set(scenario.resultId, scenarioLabel(scenario.scenarioName));
    }
    return (resultId: string) => labels.get(resultId) ?? resultId;
  }, [assessment]);
}

function citationChips(
  citations: readonly Citation[],
  resultLabel: (resultId: string) => string,
): EvidenceChipModel[] {
  const chips = new Map<string, EvidenceChipModel>();
  for (const citation of citations) {
    const chip = citationChip(citation, resultLabel);
    chips.set(chip.key, chip);
  }
  return [...chips.values()];
}

export function UnderwritingWorksheet({
  caseId,
  load = fetchUnderwritingAssessment,
}: {
  caseId: string;
  load?: (caseId: string) => Promise<UnderwritingAssessmentView>;
}) {
  const [view, setView] = useState<UnderwritingAssessmentView | null>(null);
  const [error, setError] = useState<ApiClientError | null>(null);
  const [notReady, setNotReady] = useState(false);
  const [loading, setLoading] = useState(true);

  // One load routine used by both the initial effect (with an unmount guard)
  // and the retry button. An `isActive` predicate lets the effect drop results
  // that resolve after the component (or a caseId change) has moved on.
  const runLoad = useCallback(
    async (isActive: () => boolean = () => true) => {
      if (isActive()) {
        setLoading(true);
        setError(null);
        setNotReady(false);
      }
      try {
        const next = await load(caseId);
        if (isActive()) setView(next);
      } catch (requestError) {
        if (!isActive()) return;
        setView(null);
        if (
          requestError instanceof ApiClientError &&
          requestError.code === UNDERWRITING_NOT_AVAILABLE
        ) {
          setNotReady(true);
        } else if (requestError instanceof ApiClientError) {
          setError(requestError);
        } else {
          setError(
            new ApiClientError(
              0,
              "REQUEST_FAILED",
              "Không thể kết nối để đọc bản phân tích thẩm định.",
              true,
            ),
          );
        }
      } finally {
        if (isActive()) setLoading(false);
      }
    },
    [caseId, load],
  );

  useEffect(() => {
    let active = true;
    void runLoad(() => active);
    return () => {
      active = false;
    };
  }, [runLoad]);

  return (
    <>
      <CaseNav caseId={caseId} current="tham-dinh" />
      <div className="page-heading">
        <p className="eyebrow">Thẩm định tín dụng</p>
        <h1>Hồ sơ làm việc thẩm định</h1>
        <p>
          Bảng làm việc tổng hợp số liệu tài chính đã xác nhận, các chỉ số tính
          toán tự động và căn cứ chứng cứ kèm theo. Hệ thống chỉ chuẩn bị và rà
          soát bằng chứng; cán bộ có thẩm quyền là người quyết định.
        </p>
      </div>

      <div aria-live="polite" className={styles.liveRegion}>
        {loading ? "Đang tải bản phân tích thẩm định." : ""}
      </div>

      {loading ? (
        <div
          aria-busy="true"
          aria-label="Đang tải bản phân tích thẩm định"
          className="case-skeleton"
          role="status"
        >
          <span className="skeleton-line skeleton-line-wide" />
          <span className="skeleton-line" />
          <span className="skeleton-line skeleton-line-short" />
        </div>
      ) : notReady ? (
        <NotReadyPanel caseId={caseId} />
      ) : error || !view ? (
        <ErrorPanel error={error} onRetry={() => void runLoad()} />
      ) : (
        <WorksheetBody view={view} />
      )}
    </>
  );
}

function NotReadyPanel({ caseId }: { caseId: string }) {
  return (
    <section className={styles.emptyPanel} aria-labelledby="tham-dinh-empty">
      <p className="eyebrow">Chưa có bản phân tích</p>
      <h2 id="tham-dinh-empty">Bản thẩm định sẽ xuất hiện sau khi xử lý xong</h2>
      <p>
        Chưa có bản phân tích thẩm định cho hồ sơ này. Bản phân tích được tạo tự
        động sau khi quy trình xử lý hoàn tất và dữ kiện tài chính đã được xác
        nhận. Bạn có thể theo dõi tiến độ ở mục quy trình xử lý.
      </p>
      <Link className="button button-secondary" href={`/ho-so/${caseId}/quy-trinh`}>
        Xem quy trình xử lý
      </Link>
    </section>
  );
}

function ErrorPanel({
  error,
  onRetry,
}: {
  error: ApiClientError | null;
  onRetry: () => void;
}) {
  const message = error?.message || "Không đọc được bản phân tích thẩm định.";
  const canRetry = error ? error.retryable || error.status >= 500 || error.status === 0 : true;
  return (
    <div className="state-panel" role="alert">
      <p>{message}</p>
      <p className={styles.errorHint}>
        {canRetry
          ? "Kết nối hoặc dịch vụ tạm thời gián đoạn. Vui lòng thử tải lại."
          : "Nếu tình trạng tiếp diễn, vui lòng kiểm tra quyền truy cập hồ sơ hoặc liên hệ quản trị."}
      </p>
      <button className="button button-secondary" onClick={onRetry} type="button">
        Thử tải lại
      </button>
    </div>
  );
}

function WorksheetBody({ view }: { view: UnderwritingAssessmentView }) {
  const { assessment } = view;
  const resultLabel = useResultLabels(assessment);
  return (
    <div className={styles.worksheet}>
      <ProvenanceStrip view={view} />
      <FinancialFiguresSection assessment={assessment} />
      <RatiosSection assessment={assessment} />
      <EvidenceBasisSection assessment={assessment} resultLabel={resultLabel} />
    </div>
  );
}

function ProvenanceStrip({ view }: { view: UnderwritingAssessmentView }) {
  const { assessment, handoff } = view;
  const provenance = assessment.provenance;
  return (
    <section className={styles.card} aria-label="Nguồn gốc bản phân tích">
      <div className={styles.provHeader}>
        <div>
          <p className={styles.eyebrow}>Bản phân tích của cán bộ thẩm định</p>
          <p className={styles.provNote}>
            Phiên bản hồ sơ {view.caseVersion} · lập lúc{" "}
            {formatDateTime(provenance?.createdAt || view.createdAt)}
          </p>
        </div>
        {handoff ? (
          <Chip tone="info">{handoffStateLabel(handoff.state)}</Chip>
        ) : (
          <Chip tone="muted">Chưa chuyển bàn giao</Chip>
        )}
      </div>
      <dl className={styles.provGrid}>
        <div>
          <dt>Ảnh chụp chứng cứ</dt>
          <dd className={styles.mono}>
            {formatDateTime(provenance?.evidenceViewBuiltAt ?? "")}
          </dd>
        </div>
        <div>
          <dt>Phiên bản mẫu phân tích</dt>
          <dd className={styles.mono}>{view.promptVersion || NO_VALUE}</dd>
        </div>
        <div>
          <dt>Mã lần chạy</dt>
          <dd className={styles.mono} title={view.executionId}>
            {view.executionId || NO_VALUE}
          </dd>
        </div>
        <div>
          <dt>Mô hình phân tích</dt>
          <dd className={styles.mono}>{provenance?.modelId || NO_VALUE}</dd>
        </div>
      </dl>
    </section>
  );
}

interface FigureRow {
  name: string;
  value: number | null;
  chips: EvidenceChipModel[];
}

function FinancialFiguresSection({ assessment }: { assessment: Assessment }) {
  const rows = useMemo<FigureRow[]>(() => {
    const collected = new Map<
      string,
      { value: number | null; refs: EvidenceChipModel[] }
    >();
    for (const result of assessment.calculatorResults) {
      for (const input of result.inputs) {
        if (!(input.name in FINANCIAL_FIGURE_LABELS)) continue;
        const existing = collected.get(input.name);
        const chips = dedupeFactRefs(input.factRefs).map(factRefChip);
        if (existing) {
          if (existing.value === null && input.value !== null) {
            existing.value = input.value;
          }
          for (const chip of chips) {
            if (!existing.refs.some((item) => item.key === chip.key)) {
              existing.refs.push(chip);
            }
          }
        } else {
          collected.set(input.name, { value: input.value, refs: chips });
        }
      }
    }
    return Object.keys(FINANCIAL_FIGURE_LABELS)
      .filter((name) => collected.has(name))
      .map((name) => {
        const entry = collected.get(name);
        return {
          name,
          value: entry?.value ?? null,
          chips: entry?.refs ?? [],
        };
      });
  }, [assessment]);

  return (
    <section className={styles.card} aria-labelledby="tham-dinh-figures">
      <div className={styles.sectionHead}>
        <p className={styles.eyebrow}>Số liệu tài chính</p>
        <h2 id="tham-dinh-figures">Số liệu tài chính đã xác nhận</h2>
        <p className={styles.sectionNote}>
          Các số liệu báo cáo là đầu vào của bộ tính toán, mỗi số liệu dẫn nguồn
          từ dữ kiện đã xác nhận trong hồ sơ.
        </p>
      </div>
      {rows.length === 0 ? (
        <p className={styles.emptyNote}>
          Chưa có số liệu tài chính nào được đưa vào tính toán.
        </p>
      ) : (
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <caption className={styles.srOnly}>
              Số liệu tài chính đã xác nhận và nguồn chứng cứ
            </caption>
            <thead>
              <tr>
                <th scope="col">Chỉ tiêu</th>
                <th className={styles.numCol} scope="col">
                  Giá trị (đồng)
                </th>
                <th scope="col">Chứng cứ</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name}>
                  <th scope="row">{figureLabel(row.name)}</th>
                  <td className={styles.num}>
                    {row.value === null ? (
                      <span className={styles.missing}>Chưa có</span>
                    ) : (
                      formatCurrency(row.value)
                    )}
                  </td>
                  <td>
                    <EvidenceChipList chips={row.chips} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function OutcomeGate({ outcome }: { outcome: CalculatorOutcome }) {
  if (outcome.status === "COMPUTED") {
    return <Chip tone="ok">Đã tính</Chip>;
  }
  return <Chip tone="amber">Thiếu dữ kiện</Chip>;
}

function RatioRow({ result }: { result: CalculatorResult }) {
  const meta = metricMeta(result.calculator);
  const refChips = dedupeFactRefs(
    result.inputs.flatMap((input) => input.factRefs),
  ).map(factRefChip);
  return (
    <tr>
      <th scope="row">
        {meta.label}
        {result.outcome.status === "NOT_COMPUTABLE" ? (
          <span className={styles.reason}>{result.outcome.reason}</span>
        ) : null}
      </th>
      <td className={styles.num}>
        {result.outcome.status === "COMPUTED"
          ? formatByUnit(result.outcome.value, meta.unit)
          : NO_VALUE}
      </td>
      <td>
        <OutcomeGate outcome={result.outcome} />
      </td>
      <td>
        <EvidenceChipList chips={refChips} />
      </td>
    </tr>
  );
}

function RatiosSection({ assessment }: { assessment: Assessment }) {
  const results = assessment.calculatorResults;
  return (
    <section className={styles.card} aria-labelledby="tham-dinh-ratios">
      <div className={styles.sectionHead}>
        <p className={styles.eyebrow}>Chỉ số đánh giá</p>
        <h2 id="tham-dinh-ratios">Chỉ số tài chính tính toán</h2>
        <p className={styles.sectionNote}>
          Chỉ số được tính tự động từ số liệu đã xác nhận. Trạng thái thể hiện
          mức độ đầy đủ của dữ kiện để tính toán — không phải kết luận đạt hay
          không đạt tín dụng. Việc thiết lập ngưỡng và quyết định thuộc thẩm
          quyền của cán bộ.
        </p>
      </div>
      {results.length === 0 ? (
        <p className={styles.emptyNote}>Chưa có chỉ số nào được tính toán.</p>
      ) : (
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <caption className={styles.srOnly}>
              Chỉ số tài chính tính toán, trạng thái dữ kiện và chứng cứ
            </caption>
            <thead>
              <tr>
                <th scope="col">Chỉ số</th>
                <th className={styles.numCol} scope="col">
                  Giá trị
                </th>
                <th scope="col">Trạng thái dữ kiện</th>
                <th scope="col">Chứng cứ</th>
              </tr>
            </thead>
            <tbody>
              {results.map((result) => (
                <RatioRow key={result.resultId} result={result} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {assessment.trendResults.length > 0 ? (
        <TrendBlock trends={assessment.trendResults} />
      ) : null}
      {assessment.scenarioResults.length > 0 ? (
        <ScenarioBlock scenarios={assessment.scenarioResults} />
      ) : null}
    </section>
  );
}

function outcomeText(outcome: CalculatorOutcome, kind: "amount" | "growth"): string {
  if (outcome.status !== "COMPUTED") return NO_VALUE;
  return kind === "growth"
    ? formatGrowth(outcome.value)
    : formatCurrency(outcome.value);
}

function TrendBlock({ trends }: { trends: readonly TrendResult[] }) {
  return (
    <div className={styles.subBlock}>
      <h3 className={styles.subHead}>Xu hướng kỳ trước và kỳ hiện tại</h3>
      <div className={styles.tableScroll}>
        <table className={styles.table}>
          <caption className={styles.srOnly}>
            Biến động chỉ tiêu qua các kỳ
          </caption>
          <thead>
            <tr>
              <th scope="col">Chỉ tiêu</th>
              <th scope="col">Từ kỳ</th>
              <th scope="col">Đến kỳ</th>
              <th className={styles.numCol} scope="col">
                Thay đổi
              </th>
              <th className={styles.numCol} scope="col">
                Tăng trưởng
              </th>
              <th scope="col">Chứng cứ</th>
            </tr>
          </thead>
          <tbody>
            {trends.map((trend) => {
              const refChips = dedupeFactRefs(
                trend.points.flatMap((point) => point.factRefs),
              ).map(factRefChip);
              if (trend.steps.length === 0) {
                return (
                  <tr key={trend.resultId}>
                    <th scope="row">{metricLabel(trend.metric)}</th>
                    <td colSpan={4} className={styles.missing}>
                      Chưa đủ kỳ để phân tích xu hướng
                    </td>
                    <td>
                      <EvidenceChipList chips={refChips} />
                    </td>
                  </tr>
                );
              }
              return trend.steps.map((step, index) => (
                <tr key={`${trend.resultId}-${step.fromPeriod}-${step.toPeriod}`}>
                  <th scope="row">{metricLabel(trend.metric)}</th>
                  <td>{periodLabel(step.fromPeriod)}</td>
                  <td>{periodLabel(step.toPeriod)}</td>
                  <td className={styles.num}>{outcomeText(step.delta, "amount")}</td>
                  <td className={styles.num}>
                    {outcomeText(step.growthRate, "growth")}
                  </td>
                  <td>
                    {index === 0 ? <EvidenceChipList chips={refChips} /> : null}
                  </td>
                </tr>
              ));
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ScenarioBlock({ scenarios }: { scenarios: readonly ScenarioResult[] }) {
  return (
    <div className={styles.subBlock}>
      <h3 className={styles.subHead}>Kịch bản suy giảm được kiểm thử</h3>
      {scenarios.map((scenario) => (
        <div className={styles.scenario} key={scenario.resultId}>
          <p className={styles.scenarioTitle}>{scenarioLabel(scenario.scenarioName)}</p>
          {scenario.adjustments.length > 0 ? (
            <p className={styles.scenarioAdj}>
              Điều chỉnh:{" "}
              {scenario.adjustments
                .map(
                  (adj) =>
                    `${metricLabel(adj.metric)} ${formatGrowth(adj.relativeChange)}`,
                )
                .join(" · ")}
            </p>
          ) : null}
          <div className={styles.tableScroll}>
            <table className={styles.table}>
              <caption className={styles.srOnly}>
                So sánh giá trị gốc và giá trị điều chỉnh theo kịch bản
              </caption>
              <thead>
                <tr>
                  <th scope="col">Chỉ tiêu</th>
                  <th className={styles.numCol} scope="col">
                    Giá trị gốc
                  </th>
                  <th className={styles.numCol} scope="col">
                    Sau điều chỉnh
                  </th>
                </tr>
              </thead>
              <tbody>
                {scenario.metrics.map((metric) => (
                  <tr key={`${scenario.resultId}-${metric.metric}`}>
                    <th scope="row">{metricLabel(metric.metric)}</th>
                    <td className={styles.num}>{outcomeText(metric.base, "amount")}</td>
                    <td className={styles.num}>
                      {outcomeText(metric.adjusted, "amount")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function FindingCard({
  finding,
  resultLabel,
}: {
  finding: Finding;
  resultLabel: (resultId: string) => string;
}) {
  const chips = citationChips(finding.citations, resultLabel);
  return (
    <li className={styles.finding}>
      <p className={styles.findingText}>{finding.statementVi || NO_VALUE}</p>
      <div className={styles.findingMeta}>
        <Chip tone={confidenceTone(finding.confidence)}>
          {CONFIDENCE_LABELS[finding.confidence] ?? finding.confidence}
        </Chip>
        <EvidenceChipList chips={chips} />
      </div>
      {finding.uncertaintyVi ? (
        <p className={styles.uncertainty}>Điểm chưa chắc chắn: {finding.uncertaintyVi}</p>
      ) : null}
    </li>
  );
}

function confidenceTone(confidence: string): Tone {
  if (confidence === "HIGH") return "ok";
  if (confidence === "LOW") return "amber";
  return "info";
}

function FindingGroup({
  title,
  findings,
  resultLabel,
}: {
  title: string;
  findings: readonly Finding[];
  resultLabel: (resultId: string) => string;
}) {
  if (findings.length === 0) return null;
  return (
    <div className={styles.findingGroup}>
      <h3 className={styles.subHead}>{title}</h3>
      <ul className={styles.findingList}>
        {findings.map((finding, index) => (
          <FindingCard
            finding={finding}
            key={`${title}-${index}`}
            resultLabel={resultLabel}
          />
        ))}
      </ul>
    </div>
  );
}

function EvidenceBasisSection({
  assessment,
  resultLabel,
}: {
  assessment: Assessment;
  resultLabel: (resultId: string) => string;
}) {
  const structure = assessment.proposedStructure;
  const hasNarrative =
    assessment.business.findings.length > 0 ||
    assessment.financial.findings.length > 0 ||
    assessment.cashFlow.findings.length > 0 ||
    assessment.repaymentSource.findings.length > 0 ||
    assessment.repaymentSource.downsideScenarios.length > 0 ||
    (structure?.findings.length ?? 0) > 0;

  return (
    <section className={styles.card} aria-labelledby="tham-dinh-basis">
      <div className={styles.sectionHead}>
        <p className={styles.eyebrow}>Căn cứ chứng cứ</p>
        <h2 id="tham-dinh-basis">Căn cứ chứng cứ và nhận định</h2>
        <p className={styles.sectionNote}>
          Mỗi nhận định đều dẫn chiếu chứng cứ và mức độ tin cậy. Giả định được
          nêu tách biệt và chưa được xác minh; khoảng trống chứng cứ được liệt kê
          để cán bộ bổ sung.
        </p>
      </div>

      {structure ? <ProposedStructureCard structure={structure} /> : null}

      {hasNarrative ? (
        <>
          <FindingGroup
            findings={assessment.business.findings}
            resultLabel={resultLabel}
            title="Đánh giá hoạt động kinh doanh"
          />
          <FindingGroup
            findings={assessment.financial.findings}
            resultLabel={resultLabel}
            title="Đánh giá tài chính"
          />
          <FindingGroup
            findings={assessment.cashFlow.findings}
            resultLabel={resultLabel}
            title="Đánh giá dòng tiền"
          />
          <FindingGroup
            findings={assessment.repaymentSource.findings}
            resultLabel={resultLabel}
            title="Nguồn trả nợ"
          />
          <FindingGroup
            findings={assessment.repaymentSource.downsideScenarios}
            resultLabel={resultLabel}
            title="Kịch bản suy giảm nguồn trả nợ"
          />
        </>
      ) : (
        <p className={styles.emptyNote}>
          Chưa có nhận định định tính nào được ghi nhận cho hồ sơ này.
        </p>
      )}

      {assessment.risks.length > 0 || assessment.mitigants.length > 0 ? (
        <RisksBlock
          mitigants={assessment.mitigants}
          resultLabel={resultLabel}
          risks={assessment.risks}
        />
      ) : null}

      {assessment.assumptions.length > 0 ? (
        <AssumptionsBlock
          assumptions={assessment.assumptions}
          resultLabel={resultLabel}
        />
      ) : null}

      <EvidenceGapsBlock gaps={assessment.evidenceGaps} />
    </section>
  );
}

function ProposedStructureCard({
  structure,
}: {
  structure: NonNullable<Assessment["proposedStructure"]>;
}) {
  return (
    <div className={styles.structureCard}>
      <div className={styles.structureHead}>
        <h3 className={styles.subHead}>Cấu trúc tài trợ đề xuất</h3>
        <Chip tone="muted">Dự thảo · chưa quyết định</Chip>
      </div>
      <dl className={styles.structureGrid}>
        <div>
          <dt>Hình thức</dt>
          <dd>{structure.instrumentVi || NO_VALUE}</dd>
        </div>
        <div>
          <dt>Số tiền đề xuất</dt>
          <dd className={styles.mono}>{formatCurrency(structure.proposedAmountVnd)}</dd>
        </div>
        <div>
          <dt>Thời hạn</dt>
          <dd className={styles.mono}>
            {structure.tenorMonths === null
              ? NO_VALUE
              : `${structure.tenorMonths} tháng`}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function RisksBlock({
  risks,
  mitigants,
  resultLabel,
}: {
  risks: readonly RiskItem[];
  mitigants: readonly RiskItem[];
  resultLabel: (resultId: string) => string;
}) {
  const mitigantsByRisk = useMemo(() => {
    const grouped = new Map<string, RiskItem[]>();
    for (const mitigant of mitigants) {
      const list = grouped.get(mitigant.riskId) ?? [];
      list.push(mitigant);
      grouped.set(mitigant.riskId, list);
    }
    return grouped;
  }, [mitigants]);

  return (
    <div className={styles.findingGroup}>
      <h3 className={styles.subHead}>Rủi ro sơ bộ và biện pháp giảm thiểu</h3>
      <ul className={styles.findingList}>
        {risks.map((risk) => {
          const related = mitigantsByRisk.get(risk.riskId) ?? [];
          return (
            <li className={styles.finding} key={risk.riskId}>
              <p className={styles.findingText}>{risk.descriptionVi || NO_VALUE}</p>
              <div className={styles.findingMeta}>
                <Chip tone={confidenceTone(risk.confidence)}>
                  {CONFIDENCE_LABELS[risk.confidence] ?? risk.confidence}
                </Chip>
                <EvidenceChipList chips={citationChips(risk.citations, resultLabel)} />
              </div>
              {related.length > 0 ? (
                <div className={styles.mitigants}>
                  <p className={styles.mitigantLabel}>Biện pháp giảm thiểu</p>
                  <ul>
                    {related.map((mitigant, index) => (
                      <li key={`${risk.riskId}-mit-${index}`}>
                        <span>{mitigant.descriptionVi || NO_VALUE}</span>
                        <EvidenceChipList
                          chips={citationChips(mitigant.citations, resultLabel)}
                        />
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function AssumptionsBlock({
  assumptions,
  resultLabel,
}: {
  assumptions: readonly Assessment["assumptions"][number][];
  resultLabel: (resultId: string) => string;
}) {
  return (
    <div className={styles.findingGroup}>
      <div className={styles.structureHead}>
        <h3 className={styles.subHead}>Giả định của cán bộ</h3>
        <Chip tone="amber">Chưa được xác minh</Chip>
      </div>
      <ul className={styles.findingList}>
        {assumptions.map((assumption, index) => (
          <li className={styles.finding} key={`assumption-${index}`}>
            <p className={styles.findingText}>{assumption.statementVi || NO_VALUE}</p>
            <p className={styles.uncertainty}>Cơ sở: {assumption.rationaleVi || NO_VALUE}</p>
            {assumption.basisCitations.length > 0 ? (
              <div className={styles.findingMeta}>
                <EvidenceChipList
                  chips={citationChips(assumption.basisCitations, resultLabel)}
                />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function EvidenceGapsBlock({ gaps }: { gaps: readonly EvidenceGapItem[] }) {
  if (gaps.length === 0) {
    return (
      <div className={styles.findingGroup}>
        <h3 className={styles.subHead}>Khoảng trống chứng cứ</h3>
        <p className={styles.emptyNote}>
          Không ghi nhận khoảng trống chứng cứ nào cần bổ sung.
        </p>
      </div>
    );
  }
  return (
    <div className={styles.findingGroup}>
      <h3 className={styles.subHead}>Khoảng trống chứng cứ cần bổ sung</h3>
      <ul className={styles.gapList}>
        {gaps.map((gap, index) => (
          <li className={styles.gap} key={`gap-${index}`}>
            <div className={styles.gapHead}>
              <p className={styles.findingText}>{gap.missingInformationVi || NO_VALUE}</p>
              <Chip tone={gapTone(gap.blockingLevel)}>
                {GAP_LEVEL_LABELS[gap.blockingLevel] ?? gap.blockingLevel}
              </Chip>
            </div>
            <p className={styles.uncertainty}>{gap.whyNeededVi}</p>
            {gap.suggestedEvidenceVi.length > 0 ? (
              <p className={styles.gapSuggest}>
                Gợi ý bổ sung: {gap.suggestedEvidenceVi.join("; ")}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function gapTone(level: string): Tone {
  if (level === "BLOCKING") return "risk";
  if (level === "CLARIFICATION") return "info";
  return "amber";
}
