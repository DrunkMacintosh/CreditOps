import React from "react";

import type {
  AssumptionItem,
  EvidenceGapItem,
  ExceptionCategory,
  ExceptionItem,
  GapBlockingLevel,
  LegalAssessmentBody,
  OwnershipInconsistency,
  PolicyCorpusRef,
  PolicyFinding,
  PolicyHitRecord,
} from "../../lib/api/legal";
import {
  ConfidenceNote,
  EvidenceChips,
  FindingList,
  GateChip,
  legalStyles as styles,
  type GateTone,
} from "./legal-primitives";

// --- entity / authority / ownership review -----------------------------------

function OwnershipInconsistencyCard({ item }: { item: OwnershipInconsistency }) {
  return (
    <li className={`${styles.card} ${styles.cardRisk}`}>
      <div className={styles.cardHead}>
        <h3 className={styles.cardTitle}>Chênh lệch tên chủ sở hữu</h3>
        <GateChip label="Cần đối chiếu" tone="risk" />
      </div>
      <p className={styles.body}>{item.descriptionVi}</p>
      <EvidenceChips citations={item.citations} />
      <div className={styles.metaRow}>
        <ConfidenceNote confidence={item.confidence} />
      </div>
    </li>
  );
}

export function LegalReviewSection({ assessment }: { assessment: LegalAssessmentBody }) {
  const { legalEntityReview, authoritySignatoryReview, ownershipConsistency } = assessment;
  return (
    <section aria-labelledby="legal-review-heading" className={styles.section}>
      <div className={styles.sectionHead}>
        <h2 className={styles.sectionTitle} id="legal-review-heading">
          Rà soát pháp nhân và thẩm quyền
        </h2>
        <p className={styles.sectionCopy}>
          Nhận định về tư cách pháp nhân, thẩm quyền ký kết và tính nhất quán chủ sở hữu.
          Mỗi nhận định đều dẫn chiếu chứng cứ.
        </p>
      </div>

      {ownershipConsistency.inconsistencies.length > 0 ? (
        <ul className={styles.stack}>
          {ownershipConsistency.inconsistencies.map((item, index) => (
            <OwnershipInconsistencyCard item={item} key={index} />
          ))}
        </ul>
      ) : null}

      <div className={styles.card}>
        <h3 className={styles.cardTitle}>Tư cách pháp nhân</h3>
        <FindingList
          findings={legalEntityReview}
          emptyLabel="Chưa có nhận định về tư cách pháp nhân."
        />
      </div>

      <div className={styles.card}>
        <h3 className={styles.cardTitle}>Thẩm quyền ký kết</h3>
        <FindingList
          findings={authoritySignatoryReview}
          emptyLabel="Chưa có nhận định về thẩm quyền ký kết."
        />
      </div>

      <div className={styles.card}>
        <h3 className={styles.cardTitle}>Nhất quán chủ sở hữu</h3>
        <FindingList
          findings={ownershipConsistency.findings}
          emptyLabel="Chưa có nhận định về tính nhất quán chủ sở hữu."
        />
      </div>
    </section>
  );
}

// --- policy review -----------------------------------------------------------

export function PolicyReviewSection({
  policyReview,
  policyHits,
  policyCorpusRef,
}: {
  policyReview: PolicyFinding[];
  policyHits: PolicyHitRecord[];
  policyCorpusRef: PolicyCorpusRef | null;
}) {
  const corpusConfigured = policyCorpusRef !== null || policyHits.length > 0;
  return (
    <section aria-labelledby="legal-policy-heading" className={styles.section}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionTitleRow}>
          <h2 className={styles.sectionTitle} id="legal-policy-heading">
            Rà soát chính sách
          </h2>
          <span className={styles.sectionCount}>
            {policyHits.length} điều khoản tham chiếu
          </span>
        </div>
        <p className={styles.sectionCopy}>
          Đối chiếu với kho chính sách tổng hợp (dữ liệu mô phỏng, không phải chính sách
          chính thức của ngân hàng). Mỗi nhận định trích dẫn nguyên văn điều khoản.
        </p>
      </div>

      {policyCorpusRef ? (
        <p className={styles.provenanceLine}>
          <span>
            Kho chính sách: {policyCorpusRef.corpusId} · {policyCorpusRef.version}
          </span>
          {policyCorpusRef.isSynthetic ? (
            <span className={styles.mockTag}>Tổng hợp</span>
          ) : null}
        </p>
      ) : null}

      {!corpusConfigured ? (
        <p className={styles.empty}>
          Chưa có kho chính sách tổng hợp được cấu hình cho hồ sơ này, nên hệ thống không
          đưa ra nhận định chính sách nào. Khoảng trống này được ghi lại bên dưới để bổ sung.
        </p>
      ) : policyReview.length === 0 ? (
        <p className={styles.empty}>Không có phát hiện chính sách nào cần lưu ý.</p>
      ) : (
        <ul className={styles.stack}>
          {policyReview.map((finding, index) => (
            <li className={styles.card} key={index}>
              <p className={styles.body}>{finding.possibleIssueVi}</p>
              <EvidenceChips citations={finding.citations} />
              <div className={styles.metaRow}>
                <ConfidenceNote confidence={finding.confidence} />
              </div>
              {finding.uncertaintyVi ? (
                <p className={styles.uncertainty}>
                  <b>Điểm chưa chắc chắn:</b> {finding.uncertaintyVi}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --- exceptions --------------------------------------------------------------

const CATEGORY_LABEL: Record<ExceptionCategory, string> = {
  POLICY: "Chính sách",
  LEGAL: "Pháp lý",
  COLLATERAL: "Tài sản bảo đảm",
};

export function ExceptionsSection({ exceptions }: { exceptions: ExceptionItem[] }) {
  return (
    <section aria-labelledby="legal-exceptions-heading" className={styles.section}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionTitleRow}>
          <h2 className={styles.sectionTitle} id="legal-exceptions-heading">
            Ngoại lệ cần rà soát
          </h2>
          <span className={styles.sectionCount}>{exceptions.length} ngoại lệ</span>
        </div>
        <p className={styles.sectionCopy}>
          Các điểm cần con người xem xét — nêu ra để rà soát, không phải kết luận pháp lý.
        </p>
      </div>

      {exceptions.length === 0 ? (
        <p className={styles.empty}>Không có ngoại lệ nào được nêu để rà soát.</p>
      ) : (
        <ul className={styles.stack}>
          {exceptions.map((item, index) => (
            <li className={`${styles.card} ${styles.cardAmber}`} key={index}>
              <div className={styles.cardHead}>
                <p className={styles.body} style={{ margin: 0 }}>
                  {item.possibleIssueVi}
                </p>
                {item.category ? (
                  <span className={styles.categoryTag}>{CATEGORY_LABEL[item.category]}</span>
                ) : null}
              </div>
              <EvidenceChips citations={item.citations} />
              <div className={styles.metaRow}>
                <ConfidenceNote confidence={item.confidence} />
              </div>
              {item.uncertaintyVi ? (
                <p className={styles.uncertainty}>
                  <b>Điểm chưa chắc chắn:</b> {item.uncertaintyVi}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --- evidence gaps -----------------------------------------------------------

interface Gate {
  label: string;
  tone: GateTone;
}

const BLOCKING_GATE: Record<GapBlockingLevel, Gate> = {
  BLOCKING: { label: "Chặn", tone: "risk" },
  CONDITIONAL: { label: "Có điều kiện", tone: "amber" },
  CLARIFICATION: { label: "Cần làm rõ", tone: "info" },
};

const BLOCKING_ORDER: Record<string, number> = {
  BLOCKING: 0,
  CONDITIONAL: 1,
  CLARIFICATION: 2,
};

function gapBand(level: GapBlockingLevel | null): string {
  if (level === "BLOCKING") return `${styles.card} ${styles.cardRisk}`;
  if (level === "CONDITIONAL") return `${styles.card} ${styles.cardAmber}`;
  return styles.card;
}

export function EvidenceGapsSection({ gaps }: { gaps: EvidenceGapItem[] }) {
  const ordered = gaps
    .map((gap, index) => ({ gap, index }))
    .sort((a, b) => {
      const rankA = a.gap.blockingLevel ? BLOCKING_ORDER[a.gap.blockingLevel] : 3;
      const rankB = b.gap.blockingLevel ? BLOCKING_ORDER[b.gap.blockingLevel] : 3;
      return rankA - rankB || a.index - b.index;
    })
    .map((entry) => entry.gap);

  return (
    <section aria-labelledby="legal-gaps-heading" className={styles.section}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionTitleRow}>
          <h2 className={styles.sectionTitle} id="legal-gaps-heading">
            Khoảng trống chứng cứ
          </h2>
          <span className={styles.sectionCount}>{gaps.length} khoảng trống</span>
        </div>
        <p className={styles.sectionCopy}>
          Thông tin còn thiếu để hoàn tất rà soát pháp lý. Khoảng trống chặn cần được bổ sung
          trước.
        </p>
      </div>

      {ordered.length === 0 ? (
        <p className={styles.empty}>Không còn khoảng trống chứng cứ nào được ghi nhận.</p>
      ) : (
        <ul className={styles.stack}>
          {ordered.map((gap, index) => {
            const gate = gap.blockingLevel ? BLOCKING_GATE[gap.blockingLevel] : null;
            return (
              <li className={gapBand(gap.blockingLevel)} key={index}>
                <div className={styles.cardHead}>
                  <h3 className={styles.cardTitle}>{gap.missingInformationVi}</h3>
                  {gate ? <GateChip label={gate.label} tone={gate.tone} /> : null}
                </div>
                {gap.whyNeededVi ? (
                  <p className={styles.uncertainty}>
                    <b>Vì sao cần:</b> {gap.whyNeededVi}
                  </p>
                ) : null}
                {gap.suggestedEvidenceVi.length > 0 ? (
                  <ul className={styles.suggest}>
                    {gap.suggestedEvidenceVi.map((suggestion, suggestionIndex) => (
                      <li key={suggestionIndex}>{suggestion}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// --- assumptions -------------------------------------------------------------

export function AssumptionsSection({ assumptions }: { assumptions: AssumptionItem[] }) {
  if (assumptions.length === 0) return null;
  return (
    <section aria-labelledby="legal-assumptions-heading" className={styles.section}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionTitleRow}>
          <h2 className={styles.sectionTitle} id="legal-assumptions-heading">
            Giả định
          </h2>
          <span className={styles.sectionCount}>{assumptions.length} giả định</span>
        </div>
        <p className={styles.sectionCopy}>
          Những giả định đã dùng trong quá trình rà soát, kèm căn cứ.
        </p>
      </div>
      <ul className={styles.stack}>
        {assumptions.map((item, index) => (
          <li className={styles.card} key={index}>
            <p className={styles.body}>{item.statementVi}</p>
            {item.rationaleVi ? (
              <p className={styles.uncertainty}>
                <b>Căn cứ:</b> {item.rationaleVi}
              </p>
            ) : null}
            <EvidenceChips citations={item.basisCitations} />
          </li>
        ))}
      </ul>
    </section>
  );
}
