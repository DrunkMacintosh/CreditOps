"use client";

import React, { useCallback, useEffect, useState } from "react";

import { creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type { CreditCaseDto } from "../../lib/api/contracts";
import { CaseNav } from "../shell/case-nav";
import styles from "./handoff-summary.module.css";

// Mirrors services/api/src/creditops/domain/handoffs.py. Wire contract for
// fetching/creating a handoff is contract-pending (plan Task 9); this view
// type only shapes data already provided by a caller via props.
export interface HandoffView {
  id: string;
  caseVersion: number;
  state: "READY_FOR_SPECIALIST_REVIEW"; // only canonical state
  stale: boolean;
  confirmedFactCount: number;
  conflictCount: number;
  gapCount: number;
  createdAt: string | null;
}

const STATE_LABELS_VI: Record<HandoffView["state"], string> = {
  READY_FOR_SPECIALIST_REVIEW: "Sẵn sàng cho chuyên viên thẩm định",
};

export function HandoffSummary({ handoff }: { handoff: HandoffView }) {
  return (
    <section aria-labelledby="handoff-summary-heading" className={styles.summary}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>Gói bàn giao</p>
        <h2 className={styles.title} id="handoff-summary-heading">
          Gói bàn giao chuyên viên
        </h2>
        <span className={styles.boundary}>Không phải quyết định tín dụng</span>
      </header>

      <span className={styles.gateChip}>
        <span aria-hidden="true" className={styles.gateDot} />
        {STATE_LABELS_VI[handoff.state]}
      </span>

      <p className={styles.recipientNote}>
        Gói này chuẩn bị chứng cứ để chuyên viên thẩm định rà soát. Hệ thống không quyết định cấp
        hay từ chối tín dụng.
      </p>

      {handoff.stale ? (
        <div className={styles.staleWarning} role="alert">
          <span className={styles.staleBadge}>Đã lỗi thời</span>
          <p>Gói bàn giao đã lỗi thời do hồ sơ thay đổi. Cần tạo lại sau khi xử lý thay đổi.</p>
        </div>
      ) : null}

      <dl className={styles.counts}>
        <div>
          <dt>Dữ kiện đã xác nhận</dt>
          <dd>{handoff.confirmedFactCount}</dd>
        </div>
        <div>
          <dt>Mâu thuẫn</dt>
          <dd>{handoff.conflictCount}</dd>
        </div>
        <div>
          <dt>Khoảng trống</dt>
          <dd>{handoff.gapCount}</dd>
        </div>
      </dl>

      <div className={styles.manifest}>
        <span className={styles.reference}>
          <span aria-hidden="true" className={styles.referenceDot} />
          Mã gói: {handoff.id} · phiên bản {handoff.caseVersion}
        </span>
        <p className={styles.metaLine}>Phiên bản hồ sơ: {handoff.caseVersion}</p>
        {handoff.createdAt ? (
          <p className={styles.metaLine}>Thời điểm tạo: {formatViDateTime(handoff.createdAt)}</p>
        ) : null}
      </div>
    </section>
  );
}

function formatViDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}

const HANDOFF_CONTRACT_PENDING_TEXT =
  "Gói bàn giao chưa khả dụng: máy chủ chưa công bố hợp đồng API bàn giao (kế hoạch Task 9).";

// Client loader for app/ho-so/[caseId]/ban-giao/page.tsx. The handoff wire
// contract (plan Task 9) is not canonically pinned, so this loads only the
// canonical api.getCase and renders an explicit contract-pending state. No
// handoff fetch happens here — this is intentional fail-closed behavior.
export function HandoffWorkspace({
  caseId,
  api = creditOpsApi,
}: {
  caseId: string;
  api?: Pick<typeof creditOpsApi, "getCase">;
}) {
  const [creditCase, setCreditCase] = useState<CreditCaseDto | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCreditCase(await api.getCase(caseId));
    } catch (requestError) {
      setError(getVietnameseApiError(requestError));
    } finally {
      setLoading(false);
    }
  }, [api, caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div aria-busy="true" aria-label="Đang tải hồ sơ" className="case-skeleton" role="status">
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (error || !creditCase) {
    return (
      <div className="state-panel" role="alert">
        <p>{error ?? "Không thể đọc hồ sơ."}</p>
        <button className="button button-secondary" onClick={() => void load()} type="button">
          Thử tải lại
        </button>
      </div>
    );
  }

  return (
    <>
      <CaseNav caseId={caseId} current="ban-giao" />
      <div className="page-heading">
        <p className="eyebrow">Hồ sơ · phiên bản {creditCase.version}</p>
        <h1>Bàn giao hồ sơ</h1>
        <p>Không phải quyết định tín dụng</p>
      </div>
      <div className="state-panel" role="status">
        <p>{HANDOFF_CONTRACT_PENDING_TEXT}</p>
      </div>
    </>
  );
}
