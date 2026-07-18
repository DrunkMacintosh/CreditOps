"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  getLegalErrorMessage,
  isLegalAssessmentNotReady,
  legalApi,
  type LegalApi,
  type LegalAssessment,
} from "../../lib/api/legal";
import { CaseNav } from "../shell/case-nav";
import { CollateralReview } from "./collateral-review";
import { ControlledChecksLedger } from "./controlled-checks";
import {
  AssumptionsSection,
  EvidenceGapsSection,
  ExceptionsSection,
  LegalReviewSection,
  PolicyReviewSection,
} from "./legal-narrative";
import { formatDateTime, legalStyles as styles } from "./legal-primitives";

const HANDOFF_STATE_LABEL: Record<string, string> = {
  READY_FOR_RISK_REVIEW: "Sẵn sàng chuyển rà soát rủi ro",
};

// Read-only presentation of one legal/compliance/collateral assessment. All
// data is analysis and evidence — the screen never states a credit or legal
// decision. Split from the loader so it can be rendered directly in tests.
export function LegalAssessmentView({ data }: { data: LegalAssessment }) {
  const body = data.assessment;
  const provenance = body.provenance;
  const createdAt = provenance?.createdAt ?? data.createdAt;
  const handoffLabel = data.handoff
    ? HANDOFF_STATE_LABEL[data.handoff.state] ?? data.handoff.state
    : null;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>Pháp chế · phiên bản {data.caseVersion}</p>
        <h1 className={styles.title}>Rà soát pháp lý và tuân thủ</h1>
        <p className={styles.lede}>
          Màn hình chuẩn bị và rà soát chứng cứ pháp lý, tuân thủ và tài sản bảo đảm. Hệ thống
          không phê duyệt hay từ chối tín dụng — quyết định thuộc về cán bộ.
        </p>
        <ul className={styles.provenance}>
          <li>
            Mã rà soát: <b>{data.assessmentId.slice(0, 8) || "—"}</b>
          </li>
          <li>
            Phiên bản quy tắc: <b>{data.promptVersion || "—"}</b>
          </li>
          <li>
            Lần chạy: <b>{data.executionId.slice(0, 8) || "—"}</b>
          </li>
          <li>
            Tạo lúc: <b>{formatDateTime(createdAt)}</b>
          </li>
          {handoffLabel ? (
            <li>
              Bàn giao: <b>{handoffLabel}</b>
            </li>
          ) : null}
        </ul>
        <div className={styles.corpusNotice}>
          <div>
            <strong>Dữ liệu tổng hợp dùng cho trình diễn.</strong> Kho chính sách, kết quả
            kiểm tra kiểm soát và tài liệu đều là dữ liệu mô phỏng, không phải chính sách hay
            phản hồi chính thức của ngân hàng.
            {provenance ? (
              <span className={styles.noticeRef}>
                Nguồn: {provenance.modelId || "—"} · {provenance.agentRole || "—"}
              </span>
            ) : null}
          </div>
        </div>
      </header>

      <ControlledChecksLedger
        interpretations={body.controlledCheckInterpretations}
        results={body.controlledCheckResults}
      />

      <CollateralReview
        documentItems={body.collateralReview.documentItems}
        ownershipFindings={body.collateralReview.ownershipEvidenceFindings}
      />

      <LegalReviewSection assessment={body} />

      <PolicyReviewSection
        policyCorpusRef={body.policyCorpusRef}
        policyHits={body.policyHits}
        policyReview={body.policyReview}
      />

      <ExceptionsSection exceptions={body.exceptions} />

      <EvidenceGapsSection gaps={body.evidenceGaps} />

      <AssumptionsSection assumptions={body.assumptions} />
    </div>
  );
}

// The page loader: fetches the latest assessment for the case and resolves the
// three async outcomes — an assessment, a "not ready yet" empty state, or a
// recoverable error with an inline retry. Never fabricates the missing data.
export function LegalAssessmentScreen({
  api = legalApi,
  caseId,
}: {
  api?: Pick<LegalApi, "getLegalAssessment">;
  caseId: string;
}) {
  const [data, setData] = useState<LegalAssessment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notReady, setNotReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const activeRef = useRef(true);

  const load = useCallback(async () => {
    setError(null);
    setNotReady(false);
    setLoading(true);
    try {
      const result = await api.getLegalAssessment(caseId);
      if (!activeRef.current) return;
      setData(result);
    } catch (requestError) {
      if (!activeRef.current) return;
      if (isLegalAssessmentNotReady(requestError)) {
        setData(null);
        setNotReady(true);
      } else {
        setError(getLegalErrorMessage(requestError));
      }
    } finally {
      if (activeRef.current) setLoading(false);
    }
  }, [api, caseId]);

  useEffect(() => {
    activeRef.current = true;
    void load();
    return () => {
      activeRef.current = false;
    };
  }, [load]);

  return (
    <>
      <CaseNav caseId={caseId} current="phap-che" />
      <div aria-live="polite">
        {loading ? (
          <div
            aria-busy="true"
            aria-label="Đang tải rà soát pháp chế"
            className="case-skeleton"
            role="status"
          >
            <span className="skeleton-line skeleton-line-wide" />
            <span className="skeleton-line" />
            <span className="skeleton-line skeleton-line-short" />
          </div>
        ) : error ? (
          <div className="state-panel" role="alert">
            <p>{error}</p>
            <button className="button button-secondary" onClick={() => void load()} type="button">
              Thử tải lại
            </button>
          </div>
        ) : notReady ? (
          <div className={styles.emptyPage}>
            <p className={styles.eyebrow}>Pháp chế</p>
            <h2>Chưa có bản rà soát pháp chế</h2>
            <p>
              Hồ sơ này chưa có kết quả rà soát pháp lý, tuân thủ và tài sản bảo đảm. Kết quả sẽ
              hiển thị tại đây sau khi bước đánh giá hoàn tất trong mục{" "}
              <a href={`/ho-so/${caseId}/quy-trinh`}>Quy trình</a>.
            </p>
          </div>
        ) : data ? (
          <LegalAssessmentView data={data} />
        ) : null}
      </div>
    </>
  );
}
