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
    <section aria-labelledby="audit-timeline-heading">
      <h2 id="audit-timeline-heading">Nhật ký hồ sơ</h2>
      {events.length === 0 ? (
        <p>Chưa có sự kiện nào được ghi nhận.</p>
      ) : (
        <ol aria-label="Nhật ký hồ sơ" className={styles.timeline}>
          {events.map((event) => (
            <li className={styles.event} key={event.id}>
              <time dateTime={event.createdAt}>{formatViDateTime(event.createdAt)}</time>
              <code className={styles.eventType}>{event.eventType}</code>
              <p>
                Tác nhân: {event.actorType}
                {event.actorId ? ` · ${shortId(event.actorId)}` : ""}
              </p>
              <p>
                Đối tượng: {event.artifactType} · {shortId(event.artifactId)}
              </p>
              <p>Phiên bản hồ sơ: {event.caseVersion}</p>
            </li>
          ))}
        </ol>
      )}
      {nextCursor && onLoadMore ? (
        <button
          aria-busy={loadingMore}
          className="button button-secondary"
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
