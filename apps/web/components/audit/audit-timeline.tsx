"use client";

import React, { useCallback, useEffect, useState } from "react";

import { creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type { AuditEventDto, CreditCaseDto } from "../../lib/api/contracts";
import { CaseNav } from "../shell/case-nav";
import styles from "./audit-timeline.module.css";

// Mirrors services/api/src/creditops/api/audit.py AuditEventResponse. eventData
// is metadata only (never a secret/prompt by construction) and is rendered as
// PLAIN TEXT — never as HTML.
export interface AuditEventView {
  id: string;
  caseVersion: number;
  eventType: string;
  actorType: string; // e.g. "officer" | "system" | "worker" — display raw
  actorId: string | null;
  artifactType: string;
  artifactId: string;
  eventData: Record<string, unknown>;
  createdAt: string;
}

const AUDIT_PAGE_LIMIT = 50;

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
          {events.map((event) => {
            const detail = formatEventData(event.eventData);
            return (
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
                  {detail ? (
                    // Plain text only — eventData is never rendered as HTML.
                    <p className={styles.meta}>
                      <span className={styles.metaLabel}>Chi tiết:</span> {detail}
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
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

// Renders eventData as a compact, human-readable, PLAIN-TEXT summary. React
// escapes the returned string, so no metadata value can inject markup.
function formatEventData(data: Record<string, unknown>): string {
  const entries = Object.entries(data);
  if (entries.length === 0) return "";
  return entries
    .map(([key, value]) => `${key}: ${stringifyScalar(value)}`)
    .join(" · ");
}

function stringifyScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
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

// Client loader for app/ho-so/[caseId]/nhat-ky/page.tsx. Loads the case (for
// the version line) and the first page of the cursor-paginated audit timeline,
// then appends further pages on demand — deduping by id so a shifting window
// never double-lists an event.
export function AuditWorkspace({
  caseId,
  api = creditOpsApi,
}: {
  caseId: string;
  api?: Pick<typeof creditOpsApi, "getCase" | "listAuditEvents">;
}) {
  const [creditCase, setCreditCase] = useState<CreditCaseDto | null>(null);
  const [events, setEvents] = useState<AuditEventDto[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setLoadMoreError(null);
    try {
      const [loadedCase, page] = await Promise.all([
        api.getCase(caseId),
        api.listAuditEvents(caseId, null, AUDIT_PAGE_LIMIT),
      ]);
      setCreditCase(loadedCase);
      setEvents(page.events);
      setNextCursor(page.nextCursor);
    } catch (requestError) {
      setError(getVietnameseApiError(requestError));
    } finally {
      setLoading(false);
    }
  }, [api, caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadMore = useCallback(
    async (cursor: string) => {
      setLoadingMore(true);
      setLoadMoreError(null);
      try {
        const page = await api.listAuditEvents(caseId, cursor, AUDIT_PAGE_LIMIT);
        setEvents((current) => {
          const seen = new Set(current.map((event) => event.id));
          const appended = page.events.filter((event) => !seen.has(event.id));
          return [...current, ...appended];
        });
        setNextCursor(page.nextCursor);
      } catch (requestError) {
        setLoadMoreError(getVietnameseApiError(requestError));
      } finally {
        setLoadingMore(false);
      }
    },
    [api, caseId],
  );

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
      <AuditTimeline
        events={events}
        loadingMore={loadingMore}
        nextCursor={nextCursor}
        onLoadMore={(cursor) => void loadMore(cursor)}
      />
      {loadMoreError ? (
        <div className="state-panel" role="alert">
          <p>{loadMoreError}</p>
        </div>
      ) : null}
    </>
  );
}
