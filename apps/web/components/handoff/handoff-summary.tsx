"use client";

import React, { useCallback, useEffect, useState } from "react";

import { ApiClientError, creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type { CreditCaseDto, HandoffDto } from "../../lib/api/contracts";
import { CaseNav } from "../shell/case-nav";
import styles from "./handoff-summary.module.css";

// Mirrors services/api/src/creditops/api/intake.py HandoffResponse. GET
// /handoffs is version-scoped, so a returned handoff is always current: there is
// no staleness flag and no evidence counts on this contract.
export interface HandoffView {
  handoffId: string;
  state: string;
  caseVersion: number;
  createdAt: string;
}

const STATE_LABELS_VI: Record<string, string> = {
  READY_FOR_SPECIALIST_REVIEW: "Sẵn sàng cho chuyên viên thẩm định",
};

// Fail closed on any unrecognized state — never leak a raw backend token.
const UNSUPPORTED_STATE_LABEL = "Trạng thái chưa được hỗ trợ";

function stateLabel(state: string): string {
  return STATE_LABELS_VI[state] ?? UNSUPPORTED_STATE_LABEL;
}

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
        {stateLabel(handoff.state)}
      </span>

      <p className={styles.recipientNote}>
        Gói này chuẩn bị chứng cứ để chuyên viên thẩm định rà soát. Hệ thống không quyết định cấp
        hay từ chối tín dụng.
      </p>

      <div className={styles.manifest}>
        <span className={styles.reference}>
          <span aria-hidden="true" className={styles.referenceDot} />
          Mã gói: {handoff.handoffId} · phiên bản {handoff.caseVersion}
        </span>
        <p className={styles.metaLine}>Phiên bản hồ sơ: {handoff.caseVersion}</p>
        <p className={styles.metaLine}>Thời điểm tạo: {formatViDateTime(handoff.createdAt)}</p>
      </div>
    </section>
  );
}

function formatViDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}

function isHandoffNotAvailable(error: unknown): boolean {
  return error instanceof ApiClientError && error.code === "HANDOFF_NOT_AVAILABLE";
}

// Client loader for app/ho-so/[caseId]/ban-giao/page.tsx. Loads the case (for
// the version line) and the current immutable handoff. A 404
// HANDOFF_NOT_AVAILABLE is the honest "intake not completed yet" empty state,
// not an error.
export function HandoffWorkspace({
  caseId,
  api = creditOpsApi,
}: {
  caseId: string;
  api?: Pick<typeof creditOpsApi, "getCase" | "getHandoff">;
}) {
  const [creditCase, setCreditCase] = useState<CreditCaseDto | null>(null);
  const [handoff, setHandoff] = useState<HandoffDto | null>(null);
  const [notAvailable, setNotAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setNotAvailable(false);
    try {
      const loadedCase = await api.getCase(caseId);
      setCreditCase(loadedCase);
      try {
        setHandoff(await api.getHandoff(caseId));
      } catch (handoffError) {
        if (isHandoffNotAvailable(handoffError)) {
          setHandoff(null);
          setNotAvailable(true);
        } else {
          throw handoffError;
        }
      }
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
      {handoff ? (
        <HandoffSummary handoff={handoff} />
      ) : notAvailable ? (
        <div className="state-panel" role="status">
          <p>
            Chưa có gói bàn giao cho hồ sơ này. Gói bàn giao được tạo khi cán bộ tiếp nhận
            hoàn tất tiếp nhận ở màn hình Khoảng trống chứng cứ.
          </p>
        </div>
      ) : (
        <div className="state-panel" role="alert">
          <p>Không thể đọc gói bàn giao.</p>
          <button className="button button-secondary" onClick={() => void load()} type="button">
            Thử tải lại
          </button>
        </div>
      )}
    </>
  );
}
