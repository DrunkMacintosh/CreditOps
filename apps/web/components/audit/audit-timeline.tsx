"use client";

import React, { useCallback, useEffect, useState } from "react";

import { creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type { CreditCaseDto } from "../../lib/api/contracts";
import { CaseNav } from "../shell/case-nav";
import styles from "./audit-timeline.module.css";

// Mirrors the supabase audit_events columns (plan Task 9, contract-pending).
// This view type only shapes data already provided by a caller via props.
export interface AuditEventView {
  id: string;
  caseVersion: number;
  eventType: string;
  actorType: string; // e.g. "officer" | "system" | "worker" — display raw
  actorId: string | null;
  artifactType: string;
  artifactId: string;
  createdAt: string;
}

export function AuditTimeline({
  events,
  nextCursor,
  onLoadMore,
  loadingMore = false,
}: {
  events: AuditEventView[];
  nextCursor: string | null;
  onLoadMore?: (cursor: string) => void;
  loadingMore?: boolean;
}) {
  return (
    <section aria-labelledby="audit-timeline-heading" className={styles.section}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>Nhật ký</p>
        <h2 className={styles.title} id="audit-timeline-heading">
          Nhật ký hồ sơ
        </h2>
      </header>
      {events.length === 0 ? (
        <p className={styles.empty}>Chưa có sự kiện nào được ghi nhận.</p>
      ) : (
        <ol aria-label="Nhật ký hồ sơ" className={styles.timeline}>
          {events.map((event) => (
            <li className={styles.event} key={event.id}>
              <span aria-hidden="true" className={`${styles.dot} ${eventDotClass(event.eventType)}`} />
              <div className={styles.entry}>
                <div className={styles.entryHead}>
                  <code className={styles.eventType}>{event.eventType}</code>
                  <time className={styles.time} dateTime={event.createdAt}>
                    {formatViDateTime(event.createdAt)}
                  </time>
                </div>
                <p className={styles.meta}>
                  <span className={styles.metaLabel}>Tác nhân:</span>{" "}
                  {event.actorType}
                  {event.actorId ? (
                    <>
                      {" · "}
                      <span className={styles.ref}>{shortId(event.actorId)}</span>
                    </>
                  ) : null}
                </p>
                <p className={styles.meta}>
                  <span className={styles.metaLabel}>Đối tượng:</span> {event.artifactType}
                  {" · "}
                  <span className={styles.ref}>{shortId(event.artifactId)}</span>
                </p>
                <p className={styles.metaVersion}>Phiên bản hồ sơ: {event.caseVersion}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
      {nextCursor && onLoadMore ? (
        <button
          aria-busy={loadingMore}
          className={`${styles.loadMore} button button-secondary`}
          disabled={loadingMore}
          onClick={() => onLoadMore(nextCursor)}
          type="button"
        >
          Tải thêm sự kiện
        </button>
      ) : null}
    </section>
  );
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

// The dot colour is the only signal an entry type carries — derived from the
// event verb and mapped to the shared gate/status token colours (never a loud
// background). Unknown types fall back to the calm leaf-green default.
function eventDotClass(eventType: string): string {
  const type = eventType.toUpperCase();
  const has = (...keys: string[]) => keys.some((key) => type.includes(key));
  if (has("FAIL", "BLOCK", "REJECT", "ERROR")) return styles.dotRisk;
  if (has("CONFIRM", "PASS", "SUCCEED", "SUCCESS", "COMPLETE")) return styles.dotOk;
  if (has("SUPERSED", "STALE", "CANCEL", "SKIP", "REVOK", "EXPIRE")) return styles.dotMuted;
  if (has("CREATE", "REGISTER", "UPLOAD", "SUBMIT", "START", "RECEIV", "OPEN", "RUN")) {
    return styles.dotInfo;
  }
  return styles.dotLeaf;
}

function formatViDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}

const AUDIT_CONTRACT_PENDING_TEXT =
  "Nhật ký chưa khả dụng: máy chủ chưa công bố hợp đồng API nhật ký (kế hoạch Task 9).";

// Client loader for app/ho-so/[caseId]/nhat-ky/page.tsx. The cursor-paginated
// audit log wire contract (plan Task 9) is not canonically pinned, so this
// loads only the canonical api.getCase and renders an explicit contract-pending
// state. No audit fetch happens here — this is intentional fail-closed behavior.
export function AuditWorkspace({
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
      <CaseNav caseId={caseId} current="nhat-ky" />
      <div className="page-heading">
        <p className="eyebrow">Hồ sơ · phiên bản {creditCase.version}</p>
        <h1>Nhật ký hồ sơ</h1>
      </div>
      <div className="state-panel" role="status">
        <p>{AUDIT_CONTRACT_PENDING_TEXT}</p>
      </div>
    </>
  );
}
