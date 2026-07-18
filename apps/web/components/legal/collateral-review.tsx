import React from "react";

import type { CollateralDocumentItem, CollateralDocumentStatus, LegalFinding } from "../../lib/api/legal";
import {
  EvidenceChips,
  FindingList,
  formatDate,
  GateChip,
  legalStyles as styles,
  type GateTone,
} from "./legal-primitives";

interface Gate {
  label: string;
  tone: GateTone;
}

const STATUS_GATE: Record<CollateralDocumentStatus, Gate> = {
  PRESENT: { label: "Đạt", tone: "ok" },
  MISSING: { label: "Chưa đạt", tone: "risk" },
  EXPIRED: { label: "Đã hết hạn", tone: "amber" },
};

// Missing documents first (risk band), then expired (amber), then present.
const STATUS_ORDER: Record<string, number> = {
  MISSING: 0,
  EXPIRED: 1,
  PRESENT: 2,
};

function gateFor(status: CollateralDocumentStatus | null): Gate {
  return status ? STATUS_GATE[status] : { label: "Chưa xác định", tone: "muted" };
}

function bandClass(status: CollateralDocumentStatus | null): string {
  if (status === "MISSING") return `${styles.card} ${styles.cardRisk}`;
  if (status === "EXPIRED") return `${styles.card} ${styles.cardAmber}`;
  return styles.card;
}

function orderRank(status: CollateralDocumentStatus | null): number {
  return status && status in STATUS_ORDER ? STATUS_ORDER[status] : 3;
}

export function CollateralReview({
  documentItems,
  ownershipFindings,
}: {
  documentItems: CollateralDocumentItem[];
  ownershipFindings: LegalFinding[];
}) {
  const ordered = documentItems
    .map((item, index) => ({ item, index }))
    .sort((a, b) => orderRank(a.item.status) - orderRank(b.item.status) || a.index - b.index)
    .map((entry) => entry.item);

  return (
    <section aria-labelledby="legal-collateral-heading" className={styles.section}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionTitleRow}>
          <h2 className={styles.sectionTitle} id="legal-collateral-heading">
            Hồ sơ tài sản bảo đảm
          </h2>
          <span className={styles.sectionCount}>{documentItems.length} tài liệu</span>
        </div>
        <p className={styles.sectionCopy}>
          Danh mục tài liệu bắt buộc, rà soát theo tình trạng và hạn hiệu lực trên dữ kiện
          đã xác nhận. Tài liệu thiếu hoặc hết hạn được nêu trước.
        </p>
      </div>

      {ordered.length === 0 ? (
        <p className={styles.empty}>
          Chưa có tài liệu tài sản bảo đảm nào được đánh giá. Các dữ kiện tình trạng tài liệu
          cần được xác nhận ở bước đối chiếu chứng cứ trước.
        </p>
      ) : (
        <ul className={styles.stack}>
          {ordered.map((item) => {
            const gate = gateFor(item.status);
            return (
              <li className={bandClass(item.status)} key={item.documentTypeKey}>
                <div className={styles.cardHead}>
                  <h3 className={styles.cardTitle}>{item.labelVi}</h3>
                  <GateChip label={gate.label} tone={gate.tone} />
                </div>
                {item.expiryDate || item.notesVi ? (
                  <div className={styles.detailRow}>
                    {item.expiryDate ? (
                      <span>
                        Ngày hết hạn: <b className={styles.mono}>{formatDate(item.expiryDate)}</b>
                      </span>
                    ) : null}
                    {item.notesVi ? <span>{item.notesVi}</span> : null}
                  </div>
                ) : null}
                <EvidenceChips citations={item.citations} />
              </li>
            );
          })}
        </ul>
      )}

      <div className={styles.sectionHead}>
        <h3 className={styles.sectionTitle}>Chứng cứ quyền sở hữu</h3>
      </div>
      <FindingList
        findings={ownershipFindings}
        emptyLabel="Chưa có chứng cứ quyền sở hữu nào được ghi nhận."
      />
    </section>
  );
}
