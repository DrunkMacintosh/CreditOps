"use client";

import React, { useCallback, useEffect, useState } from "react";

import { creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type { CreditCaseDto } from "../../lib/api/contracts";
import { CaseNav } from "../shell/case-nav";
import { IntakeCompletionDialog } from "./intake-completion-dialog";
import styles from "./gap-list.module.css";

// Wire contract pending: no gap-listing endpoint is canonically pinned yet
// (plan Task 9 names the file but not the path). These view types mirror the
// domain shape in services/api/src/creditops/domain/gaps.py so the UI can be
// built and tested now, ahead of the real contract.
export type GapStatus = "PROVISIONAL" | "FORMAL" | "RESOLVED" | "STALE"; // canonical enum

export interface GapView {
  id: string;
  status: GapStatus;
  issueVi: string; // mirrors domain EvidenceGap.issue_vi
  missingInformationVi: string;
  suggestedEvidenceVi: string[]; // draft suggestions, never approved requirements
}

const STATUS_LABELS_VI: Readonly<Record<GapStatus, string>> = {
  PROVISIONAL: "Tạm thời",
  FORMAL: "Chính thức",
  RESOLVED: "Đã giải quyết",
  STALE: "Đã lỗi thời",
};

const STATUS_BADGE_CLASSES: Readonly<Record<GapStatus, string>> = {
  PROVISIONAL: styles.badgeProvisional,
  FORMAL: styles.badgeFormal,
  RESOLVED: styles.badgeResolved,
  STALE: styles.badgeStale,
};

// Pure, props-driven: no fetch, no close/resolve controls — a gap can only be
// dispositioned by an authorized human through the (not-yet-built) resolution
// flow, never implicitly waived here. RESOLVED and STALE gaps stay listed so
// history is never hidden.
export function GapList({ gaps }: { gaps: GapView[] }) {
  return (
    <section aria-labelledby="gap-list-heading">
      <h2 className={styles.heading} id="gap-list-heading">
        Khoảng trống chứng cứ
      </h2>
      {gaps.length === 0 ? (
        <p className={styles.empty}>Chưa ghi nhận khoảng trống chứng cứ.</p>
      ) : (
        <ul className={styles.list}>
          {gaps.map((gap) => (
            <li className={styles.item} data-status={gap.status} key={gap.id}>
              <span className={`${styles.badge} ${STATUS_BADGE_CLASSES[gap.status]}`}>
                {STATUS_LABELS_VI[gap.status]}
              </span>
              <p className={styles.issue}>{gap.issueVi}</p>
              <p className={styles.missingInformation}>{gap.missingInformationVi}</p>
              {gap.suggestedEvidenceVi.length > 0 && (
                <div className={styles.suggestions}>
                  <p className={styles.suggestionLabel}>
                    Đề xuất tài liệu (bản nháp, chưa được phê duyệt)
                  </p>
                  <ul className={styles.suggestionList}>
                    {gap.suggestedEvidenceVi.map((suggestion, index) => (
                      // eslint-disable-next-line react/no-array-index-key
                      <li key={index}>{suggestion}</li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const GAP_CONTRACT_PENDING_TEXT =
  "Danh sách khoảng trống chưa khả dụng: máy chủ chưa công bố hợp đồng API cho khoảng trống chứng cứ (kế hoạch Task 9).";

const COMPLETION_UNAVAILABLE_REASON =
  "Hợp đồng API hoàn tất tiếp nhận chưa được công bố; thao tác sẽ khả dụng khi backend phát hành.";

// Client loader for app/ho-so/[caseId]/khoang-trong/page.tsx. Loads only the
// canonical api.getCase (for version + capabilities.canCompleteIntake); it
// performs NO gap fetch and NO completion mutation — see brief D contract
// stance. This is intentional fail-closed behavior, not a stub to "fix".
export function GapWorkspace({
  caseId,
  api = creditOpsApi,
}: {
  caseId: string;
  api?: Pick<typeof creditOpsApi, "getCase">;
}) {
  const [creditCase, setCreditCase] = useState<CreditCaseDto | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

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

  const canCompleteIntake = creditCase.capabilities.canCompleteIntake;

  return (
    <>
      <CaseNav caseId={caseId} current="khoang-trong" />
      <div className="page-heading">
        <p className="eyebrow">Hồ sơ · phiên bản {creditCase.version}</p>
        <h1>Khoảng trống chứng cứ</h1>
      </div>
      <div className="state-panel" role="status">
        <p>{GAP_CONTRACT_PENDING_TEXT}</p>
      </div>
      {canCompleteIntake && (
        <>
          <button
            className="button button-primary"
            onClick={() => setDialogOpen(true)}
            type="button"
          >
            Hoàn tất tiếp nhận…
          </button>
          <IntakeCompletionDialog
            canCompleteIntake={canCompleteIntake}
            caseVersion={creditCase.version}
            onClose={() => setDialogOpen(false)}
            onConfirm={() => {
              // Unreachable while disabled: no completion contract exists yet
              // (submitUnavailableReason keeps confirm disabled).
            }}
            open={dialogOpen}
            openGapCount={0}
            submitUnavailableReason={COMPLETION_UNAVAILABLE_REASON}
          />
        </>
      )}
    </>
  );
}
