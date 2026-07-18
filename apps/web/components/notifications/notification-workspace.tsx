"use client";

import React, { useCallback, useEffect, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  formatDateTime,
  GATE_STATUS_LABELS,
  getNotificationError,
  labelOrUnsupported,
  NOTIFICATION_NOT_DISBURSEMENT_VI,
  NotificationsApiClient,
  notificationsApi,
  shortId,
  type NotificationStatus,
} from "../../lib/api/notifications";
import { RecordActionForm } from "../gates/record-action-form";
import { CaseNav } from "../shell/case-nav";
import styles from "../gates/gates.module.css";

type NotificationApi = Pick<
  NotificationsApiClient,
  "getStatus" | "createDraft" | "approve" | "deliver"
>;

// Stage-7 credit-notification workspace. A notification is NOT a disbursement
// confirmation (the mandatory disclaimer is always shown). The draft is created
// on explicit action, approval is a rationale-bearing human gate write pinned to
// the exact draft id, and delivery records a labelled mock receipt by a
// different actor. No polling; refresh is manual; nothing completes optimistically.
export function NotificationWorkspace({
  caseId,
  api = notificationsApi,
}: {
  caseId: string;
  api?: NotificationApi;
}) {
  const [status, setStatus] = useState<NotificationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setUnauthorized(false);
    setRefreshError(null);
    try {
      setStatus(await api.getStatus(caseId));
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 403) {
        setUnauthorized(true);
      } else {
        setError(getNotificationError(requestError));
      }
    } finally {
      setLoading(false);
    }
  }, [api, caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Refetch after a write; never throws — a recorded action must not look failed
  // because a follow-up read hiccuped.
  const refresh = useCallback(async () => {
    setRefreshError(null);
    try {
      setStatus(await api.getStatus(caseId));
    } catch (requestError) {
      setRefreshError(getNotificationError(requestError));
    }
  }, [api, caseId]);

  const createDraft = useCallback(async () => {
    setCreating(true);
    setCreateError(null);
    try {
      await api.createDraft(caseId);
      await refresh();
    } catch (requestError) {
      setCreateError(getNotificationError(requestError));
    } finally {
      setCreating(false);
    }
  }, [api, caseId, refresh]);

  if (loading) {
    return (
      <div aria-busy="true" aria-label="Đang tải thông báo tín dụng" className="case-skeleton" role="status">
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (unauthorized) {
    return (
      <>
        <CaseNav caseId={caseId} current="thong-bao" />
        <div className="state-panel" role="alert">
          <p>Bạn không có vai trò tham gia hồ sơ để xem thông báo tín dụng.</p>
        </div>
      </>
    );
  }

  if (error || !status) {
    return (
      <>
        <CaseNav caseId={caseId} current="thong-bao" />
        <div className="state-panel" role="alert">
          <p>{error ?? "Không thể đọc thông báo tín dụng."}</p>
          <button className="button button-secondary" onClick={() => void load()} type="button">
            Thử tải lại
          </button>
        </div>
      </>
    );
  }

  const { draft, receipt, approvalGateStatus } = status;
  const gateSatisfied = approvalGateStatus === "SATISFIED";

  return (
    <>
      <CaseNav caseId={caseId} current="thong-bao" />
      <div className="page-heading">
        <p className="eyebrow">Giai đoạn 7 · Thông báo tín dụng</p>
        <h1>Thông báo tín dụng</h1>
      </div>

      <p className={styles.disclaimer} role="note">
        {NOTIFICATION_NOT_DISBURSEMENT_VI}
      </p>

      <div className={styles.workspace}>
        <div className={styles.gateRow}>
          <span
            className={`status-chip ${gateSatisfied ? "status-chip--ok" : "status-chip--amber"}`}
          >
            Cổng phê duyệt thông báo:{" "}
            {labelOrUnsupported(GATE_STATUS_LABELS, String(approvalGateStatus))}
          </span>
        </div>

        {draft ? (
          <>
            <section aria-labelledby="draft-heading" className={styles.section}>
              <h2 className={styles.sectionTitle} id="draft-heading">
                Nội dung bản nháp thông báo
              </h2>
              <p className={styles.entryMeta}>
                Phiên bản hồ sơ v{draft.caseVersion} · Mã nháp {shortId(draft.id)} · Quyết định
                nguồn {shortId(draft.decisionId)}
              </p>
              <pre className={styles.document}>{draft.content}</pre>
              <p className={styles.hashLine}>Content sha256: {draft.contentHash}</p>
              <p className={styles.entryMeta}>
                Người tạo {shortId(draft.createdBy)} · {formatDateTime(draft.createdAt)}
              </p>
            </section>

            {!gateSatisfied ? (
              <section aria-labelledby="approve-heading" className={styles.section}>
                <h2 className={styles.sectionTitle} id="approve-heading">
                  Phê duyệt nội dung thông báo tín dụng
                </h2>
                <p className={styles.pinned}>Áp dụng cho bản nháp: {draft.id}</p>
                <RecordActionForm
                  formatError={getNotificationError}
                  hint="Ghi phê duyệt cổng HG_CREDIT_NOTIFICATION_APPROVED cho đúng bản nháp trên. Đây không phải xác nhận giải ngân."
                  onReload={() => void refresh()}
                  onSubmit={async (rationale) => {
                    await api.approve(caseId, { draftId: draft.id, rationale });
                    await refresh();
                  }}
                  rationaleLabel="Lý do phê duyệt nội dung thông báo"
                  submitLabel="Duyệt nội dung thông báo tín dụng"
                />
              </section>
            ) : null}

            {gateSatisfied && !receipt ? (
              <section aria-labelledby="deliver-heading" className={styles.section}>
                <h2 className={styles.sectionTitle} id="deliver-heading">
                  Ghi nhận giao nhận mô phỏng
                </h2>
                <p className={styles.sectionLead}>
                  Không có gì được gửi đi. Người ghi nhận giao nhận phải khác người tạo bản nháp
                  (tách biệt nhiệm vụ); hệ thống chỉ lưu biên nhận mô phỏng và đúng content hash.
                </p>
                <RecordActionForm
                  formatError={getNotificationError}
                  hint="Ghi nhận biên nhận giao thông báo qua kênh mock."
                  onReload={() => void refresh()}
                  onSubmit={async (note) => {
                    await api.deliver(caseId, note ? { receiptNote: note } : {});
                    await refresh();
                  }}
                  rationaleLabel="Ghi chú giao nhận (không bắt buộc)"
                  rationaleRequired={false}
                  submitLabel="Ghi nhận giao nhận mô phỏng"
                />
              </section>
            ) : null}

            {receipt ? (
              <section aria-labelledby="receipt-heading" className={styles.section}>
                <h2 className={styles.sectionTitle} id="receipt-heading">
                  Biên nhận giao nhận mô phỏng
                </h2>
                <p className={styles.entryMeta}>
                  Kênh: {receipt.deliveredVia} · Mã biên nhận {shortId(receipt.id)}
                </p>
                {receipt.receiptNote ? (
                  <p className={styles.entryText}>{receipt.receiptNote}</p>
                ) : null}
                <p className={styles.hashLine}>Content sha256: {receipt.contentHash}</p>
                <p className={styles.entryMeta}>
                  Người ghi nhận {shortId(receipt.recordedBy)} ·{" "}
                  {formatDateTime(receipt.createdAt)}
                </p>
              </section>
            ) : null}
          </>
        ) : (
          <div className="empty-state">
            <p className="empty-state-title">Chưa có bản nháp thông báo tín dụng</p>
            <p className="empty-state-hint">
              Bản nháp được tạo tất định từ một quyết định phê duyệt cho phép phát hành thông
              báo. Hệ thống không tự tạo khi mở trang.
            </p>
            <div className="empty-state-action">
              <button
                aria-busy={creating}
                className="button button-primary"
                disabled={creating}
                onClick={() => void createDraft()}
                type="button"
              >
                {creating ? "Đang tạo bản nháp…" : "Tạo bản nháp thông báo tín dụng"}
              </button>
            </div>
            {createError ? (
              <div className="state-panel" role="alert">
                <p>{createError}</p>
              </div>
            ) : null}
          </div>
        )}

        {refreshError ? (
          <div className="state-panel" role="alert">
            <p>Đã ghi vào sổ, nhưng không tải lại được bản mới nhất: {refreshError}</p>
            <button className="button button-secondary" onClick={() => void refresh()} type="button">
              Tải lại
            </button>
          </div>
        ) : null}
      </div>
    </>
  );
}
