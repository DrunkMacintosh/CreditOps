import React from "react";

import type {
  ControlledCheckInterpretation,
  ControlledCheckResult,
  ControlledCheckStatus,
  ControlledCheckType,
  SubjectType,
} from "../../lib/api/legal";
import {
  ConfidenceNote,
  formatDateTime,
  GateChip,
  legalStyles as styles,
  type GateTone,
} from "./legal-primitives";

const CHECK_LABEL: Record<ControlledCheckType, string> = {
  KYC: "Định danh khách hàng (KYC)",
  AML_WATCHLIST: "Sàng lọc danh sách cảnh báo (AML)",
  RELATED_PARTY: "Kiểm tra bên liên quan",
};

const SUBJECT_LABEL: Record<SubjectType, string> = {
  ENTITY: "pháp nhân",
  INDIVIDUAL: "cá nhân",
};

interface Gate {
  label: string;
  tone: GateTone;
}

const STATUS_GATE: Record<ControlledCheckStatus, Gate> = {
  CLEAR: { label: "Đạt", tone: "ok" },
  HIT: { label: "Chưa đạt", tone: "risk" },
  INCONCLUSIVE: { label: "Cần xem xét", tone: "amber" },
};

// Not-passing (HIT) surfaces first with the risk band, then the amber
// "cần xem xét" (inconclusive), then cleared checks — so an officer sees what
// needs attention without scanning.
const STATUS_ORDER: Record<string, number> = {
  HIT: 0,
  INCONCLUSIVE: 1,
  CLEAR: 2,
};

function gateFor(status: ControlledCheckStatus | null): Gate {
  return status ? STATUS_GATE[status] : { label: "Chưa có kết quả", tone: "muted" };
}

function bandClass(status: ControlledCheckStatus | null): string {
  if (status === "HIT") return `${styles.card} ${styles.cardRisk}`;
  if (status === "INCONCLUSIVE") return `${styles.card} ${styles.cardAmber}`;
  return styles.card;
}

function orderRank(status: ControlledCheckStatus | null): number {
  return status && status in STATUS_ORDER ? STATUS_ORDER[status] : 3;
}

export function ControlledChecksLedger({
  results,
  interpretations,
}: {
  results: ControlledCheckResult[];
  interpretations: ControlledCheckInterpretation[];
}) {
  const notes = new Map<string, ControlledCheckInterpretation>();
  for (const item of interpretations) notes.set(item.invocationId, item);

  const ordered = results
    .map((result, index) => ({ result, index }))
    .sort(
      (a, b) => orderRank(a.result.status) - orderRank(b.result.status) || a.index - b.index,
    )
    .map((entry) => entry.result);

  return (
    <section aria-labelledby="legal-checks-heading" className={styles.section}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionTitleRow}>
          <h2 className={styles.sectionTitle} id="legal-checks-heading">
            Kiểm tra kiểm soát
          </h2>
          <span className={styles.sectionCount}>{results.length} kiểm tra</span>
        </div>
        <p className={styles.sectionCopy}>
          Các kiểm tra bắt buộc chạy trên dữ kiện đã xác nhận. Hệ thống chỉ ghi nhận và
          diễn giải kết quả; cán bộ là người xem xét và quyết định.
        </p>
      </div>

      {ordered.length === 0 ? (
        <p className={styles.empty}>
          Chưa có kiểm tra kiểm soát nào được thực hiện cho hồ sơ này.
        </p>
      ) : (
        <ul className={styles.stack}>
          {ordered.map((result) => {
            const gate = gateFor(result.status);
            const note = notes.get(result.invocationId);
            const subjectType = result.subjectType
              ? SUBJECT_LABEL[result.subjectType]
              : "đối tượng";
            return (
              <li className={bandClass(result.status)} key={result.invocationId}>
                <div className={styles.cardHead}>
                  <div>
                    <h3 className={styles.cardTitle}>
                      {result.checkType ? CHECK_LABEL[result.checkType] : "Kiểm tra kiểm soát"}
                    </h3>
                    {result.subjectRefVi ? (
                      <p className={styles.cardSubtitle}>
                        Đối tượng kiểm tra ({subjectType}): {result.subjectRefVi}
                      </p>
                    ) : null}
                  </div>
                  <GateChip label={gate.label} tone={gate.tone} />
                </div>

                {result.resultSummaryVi ? (
                  <p className={styles.body}>{result.resultSummaryVi}</p>
                ) : null}

                {note ? (
                  <div className={styles.interpret}>
                    <p className={styles.interpretLabel}>Diễn giải kết quả</p>
                    <p className={styles.body}>{note.statementVi}</p>
                    <div className={styles.metaRow}>
                      <ConfidenceNote confidence={note.confidence} />
                    </div>
                    {note.uncertaintyVi ? (
                      <p className={styles.uncertainty}>
                        <b>Điểm chưa chắc chắn:</b> {note.uncertaintyVi}
                      </p>
                    ) : null}
                  </div>
                ) : null}

                <div className={styles.provenanceLine}>
                  <span>
                    Công cụ: {result.toolName || "—"}
                    {result.toolVersion ? ` · ${result.toolVersion}` : ""}
                  </span>
                  {result.providerId ? <span>Nhà cung cấp: {result.providerId}</span> : null}
                  <span>Mã lần chạy: {result.invocationId.slice(0, 8) || "—"}</span>
                  <span>Thời điểm: {formatDateTime(result.invokedAt)}</span>
                  {result.isMock ? (
                    <span className={styles.mockTag}>Dữ liệu mô phỏng</span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
