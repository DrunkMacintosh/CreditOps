"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";

import type {
  Challenge,
  RecordDispositionInput,
  RiskReviewStatus,
} from "../../lib/api/risk-review";
import {
  DISPOSITION_TYPE_LABELS,
  getRiskReviewError,
  isRiskReviewNotAvailable,
  isSevere,
  labelFor,
  riskReviewApi,
  RiskReviewApiClient,
} from "../../lib/api/risk-review";
import { CaseNav } from "../shell/case-nav";
import { ChallengeCard } from "./challenge-card";
import { DispositionForm } from "./disposition-form";
import styles from "./risk-review.module.css";

const SEVERITY_RANK: Record<string, number> = {
  CRITICAL: 3,
  HIGH: 2,
  MEDIUM: 1,
  LOW: 0,
};

// Most severe first; within a severity, challenges still needing a disposition
// surface above the ones already recorded.
function orderChallenges(challenges: readonly Challenge[]): Challenge[] {
  return [...challenges].sort((a, b) => {
    const rank = (SEVERITY_RANK[b.severity] ?? -1) - (SEVERITY_RANK[a.severity] ?? -1);
    if (rank !== 0) return rank;
    const aDisposed = a.dispositions.length > 0 ? 1 : 0;
    const bDisposed = b.dispositions.length > 0 ? 1 : 0;
    return aDisposed - bDisposed;
  });
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value || "—";
}

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}

function handoffStateLabel(state: string): string {
  return state === "READY_FOR_OPERATIONS" ? "Sẵn sàng bàn giao vận hành" : state;
}

export function RiskReviewDesk({
  caseId,
  api = riskReviewApi,
}: {
  caseId: string;
  api?: Pick<
    RiskReviewApiClient,
    "getRiskReview" | "recordChallengeDisposition" | "recordAssessmentDisposition"
  >;
}) {
  const [status, setStatus] = useState<RiskReviewStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notAvailable, setNotAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const load = useCallback(
    async (withSkeleton: boolean) => {
      if (withSkeleton) setLoading(true);
      setLoadError(null);
      setNotAvailable(false);
      try {
        setStatus(await api.getRiskReview(caseId));
      } catch (requestError) {
        if (isRiskReviewNotAvailable(requestError)) {
          setNotAvailable(true);
          setStatus(null);
        } else {
          setLoadError(getRiskReviewError(requestError));
        }
      } finally {
        if (withSkeleton) setLoading(false);
      }
    },
    [api, caseId],
  );

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load(true);
    })();
    return () => {
      active = false;
    };
  }, [load]);

  // Reload after a write. Never throws: a write that succeeded must not look
  // failed just because the follow-up read hiccuped.
  const refresh = useCallback(async () => {
    setRefreshError(null);
    try {
      setStatus(await api.getRiskReview(caseId));
    } catch (requestError) {
      if (!isRiskReviewNotAvailable(requestError)) {
        setRefreshError(getRiskReviewError(requestError));
      }
    }
  }, [api, caseId]);

  const recordChallenge = useCallback(
    async (challengeId: string, input: RecordDispositionInput) => {
      await api.recordChallengeDisposition(caseId, challengeId, input);
      await refresh();
    },
    [api, caseId, refresh],
  );

  const recordAssessment = useCallback(
    async (input: RecordDispositionInput) => {
      await api.recordAssessmentDisposition(caseId, input);
      await refresh();
    },
    [api, caseId, refresh],
  );

  const ordered = useMemo(
    () => (status ? orderChallenges(status.challenges) : []),
    [status],
  );

  const metrics = useMemo(() => {
    if (!status) return null;
    const severe = status.challenges.filter((challenge) => isSevere(challenge.severity));
    const severeDisposed = severe.filter((challenge) => challenge.dispositions.length > 0).length;
    return {
      total: status.challenges.length,
      severeTotal: severe.length,
      severeDisposed,
      severeRemaining: severe.length - severeDisposed,
    };
  }, [status]);

  const header = (
    <>
      <CaseNav caseId={caseId} current="rui-ro" />
      <div className={styles.header}>
        <p className={styles.eyebrow}>Rà soát rủi ro độc lập</p>
        <h1 className={styles.pageTitle}>Bàn rà soát thách thức</h1>
        <p className={styles.lede}>
          Cán bộ rà soát rủi ro độc lập ghi quyết định cho từng thách thức mà bên
          kiểm tra đã nêu đối với hồ sơ thẩm định và pháp chế. Hệ thống chỉ chuẩn
          bị và rà soát chứng cứ; mọi quyết định tín dụng do con người thực hiện.
        </p>
      </div>
    </>
  );

  if (loading) {
    return (
      <div className={styles.page}>
        {header}
        <div
          aria-busy="true"
          aria-label="Đang tải bản rà soát rủi ro"
          className="case-skeleton"
          role="status"
        >
          <span className="skeleton-line skeleton-line-wide" />
          <span className="skeleton-line" />
        </div>
      </div>
    );
  }

  if (notAvailable) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>Chưa có bản rà soát rủi ro độc lập</p>
          <p className={styles.emptyBody}>
            Bản rà soát sẽ xuất hiện sau khi bên kiểm tra độc lập chạy trên hồ sơ
            thẩm định và pháp chế của phiên bản hồ sơ này. Chưa có thách thức nào
            để ghi quyết định.
          </p>
          <div className={styles.formActions}>
            <button
              className="button button-secondary"
              onClick={() => void load(true)}
              type="button"
            >
              Thử tải lại
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (loadError || !status || !metrics) {
    return (
      <div className={styles.page}>
        {header}
        <div className="state-panel" role="alert">
          <p>{loadError ?? "Không thể đọc bản rà soát rủi ro."}</p>
          <button
            className="button button-secondary"
            onClick={() => void load(true)}
            type="button"
          >
            Thử tải lại
          </button>
        </div>
      </div>
    );
  }

  const gateSatisfied = status.gateStatus === "SATISFIED";
  const showAssessmentForm =
    metrics.severeTotal === 0 && status.assessmentLevelDispositions.length === 0;

  const gateExplain = gateSatisfied
    ? "Cổng đã đạt: mọi điều kiện rà soát rủi ro cho phiên bản hồ sơ này đã được ghi quyết định."
    : metrics.severeTotal > 0
      ? `Cổng đạt khi mọi thách thức mức Cao/Nghiêm trọng có quyết định của cán bộ. Còn ${metrics.severeRemaining} thách thức chưa có quyết định.`
      : "Không có thách thức mức Cao/Nghiêm trọng. Cổng đạt khi cán bộ ghi nhận kết quả rà soát ở cuối trang.";

  return (
    <div className={`${styles.page} ${styles.reveal}`}>
      {header}

      <div className={styles.provenance}>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>Bản đánh giá</span>
          <span className={styles.provValue}>{shortId(status.assessmentId)}</span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>Phiên bản hồ sơ</span>
          <span className={styles.provValue}>v{status.caseVersion}</span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>Phiên bản chỉ dẫn</span>
          <span className={styles.provValue}>{status.promptVersion}</span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>Mã thực thi</span>
          <span className={styles.provValue}>{shortId(status.executionId)}</span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>Thời điểm rà soát</span>
          <span className={`${styles.provValue} ${styles.provValuePlain}`}>
            {formatDateTime(status.createdAt)}
          </span>
        </div>
        {status.handoff ? (
          <div className={styles.provItem}>
            <span className={styles.provLabel}>Bàn giao</span>
            <span className={`${styles.provValue} ${styles.provValuePlain}`}>
              {handoffStateLabel(status.handoff.state)}
            </span>
          </div>
        ) : null}
      </div>

      <section
        aria-label="Trạng thái cổng rà soát rủi ro"
        aria-live="polite"
        className={`${styles.card} ${styles.gate}`}
      >
        <div className={styles.gateHead}>
          <h2 className={styles.gateTitle}>Cổng rà soát rủi ro (G3)</h2>
          <span
            className={`${styles.chip} ${gateSatisfied ? styles.chipOk : styles.chipAmber}`}
          >
            {gateSatisfied ? "Đạt" : "Đang chờ"}
          </span>
        </div>
        <p className={styles.gateExplain}>{gateExplain}</p>
        <div className={styles.gateMetrics}>
          <div className={styles.metric}>
            <span className={styles.metricNum}>
              {metrics.severeDisposed}/{metrics.severeTotal}
            </span>
            <span className={styles.metricLabel}>Thách thức cao đã có quyết định</span>
          </div>
          <div className={styles.metric}>
            <span className={styles.metricNum}>{metrics.total}</span>
            <span className={styles.metricLabel}>Tổng số thách thức</span>
          </div>
          <div className={styles.metric}>
            <span className={styles.metricNum}>{status.unresolvedChallengeCount}</span>
            <span className={styles.metricLabel}>Chưa có quyết định</span>
          </div>
        </div>
      </section>

      {refreshError ? (
        <div className="state-panel" role="alert" style={{ marginBottom: "1.5rem" }}>
          <p>
            Quyết định đã được ghi, nhưng không tải lại được bản mới nhất: {refreshError}
          </p>
          <button
            className="button button-secondary"
            onClick={() => void refresh()}
            type="button"
          >
            Tải lại
          </button>
        </div>
      ) : null}

      <section aria-label="Danh sách thách thức">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>Thách thức cần rà soát</h2>
          <span className={styles.countPill}>{metrics.total}</span>
        </div>
        {ordered.length === 0 ? (
          <div className={styles.noneRow}>
            Bên kiểm tra độc lập không nêu thách thức nào cho phiên bản hồ sơ này.
          </div>
        ) : (
          <ul className={styles.challengeList}>
            {ordered.map((challenge) => (
              <ChallengeCard
                challenge={challenge}
                key={challenge.id}
                onRecord={(input) => recordChallenge(challenge.id, input)}
              />
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Ghi nhận ở cấp bản đánh giá" style={{ marginTop: "1.75rem" }}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>Ghi nhận kết quả rà soát</h2>
        </div>

        {status.assessmentLevelDispositions.length > 0 ? (
          <div className={styles.card}>
            <p className={styles.dispositionsHeading}>Đã ghi nhận</p>
            <div className={styles.dispositions} style={{ borderTop: "none", paddingTop: 0 }}>
              {status.assessmentLevelDispositions.map((disposition) => (
                <div className={styles.dispoItem} key={disposition.id}>
                  <div className={styles.dispoHead}>
                    <span className={styles.dispoType}>
                      {labelFor(DISPOSITION_TYPE_LABELS, disposition.dispositionType)}
                    </span>
                    <span className={styles.dispoMeta}>
                      {disposition.actorRole} · {formatDateTime(disposition.createdAt)}
                    </span>
                  </div>
                  <p className={styles.dispoNote}>{disposition.rationale}</p>
                </div>
              ))}
            </div>
          </div>
        ) : showAssessmentForm ? (
          <div className={styles.card}>
            <DispositionForm
              fixedType="NOTED"
              heading="Ghi nhận khi không có thách thức nghiêm trọng"
              hint="Không có thách thức mức Cao/Nghiêm trọng nào. Cổng G3 không tự đạt khi bên kiểm tra im lặng; cán bộ ghi nhận rõ kết quả rà soát để cổng đạt."
              onSubmit={recordAssessment}
              rationaleLabel="Nội dung ghi nhận"
              submitLabel="Ghi nhận kết quả rà soát"
            />
          </div>
        ) : (
          <div className={styles.noneRow}>
            Có thách thức mức Cao/Nghiêm trọng cần quyết định riêng ở trên; chưa cần
            ghi nhận ở cấp bản đánh giá.
          </div>
        )}
      </section>
    </div>
  );
}
