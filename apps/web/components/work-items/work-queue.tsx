"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useState } from "react";

import {
  getWorkItemsError,
  isBlocking,
  severityChip,
  shortCaseId,
  workItemsApi,
  type WorkItem,
  type WorkItemList,
} from "../../lib/api/work-items";
import styles from "./work-queue.module.css";

interface WorkQueueApi {
  listWorkItems(limit?: number): Promise<WorkItemList>;
}

interface WorkQueueProps {
  api?: WorkQueueApi;
  limit?: number;
}

// The default entry surface: a read-only list of pending work items grouped
// BLOCKING-first. No polling, no mutation — the only action is a manual refresh
// and one primary link per item to the server-supplied primaryRoute.
export function WorkQueue({ api = workItemsApi, limit }: WorkQueueProps) {
  const [collection, setCollection] = useState<WorkItemList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCollection(await api.listWorkItems(limit));
    } catch (requestError) {
      setError(getWorkItemsError(requestError));
    } finally {
      setLoading(false);
    }
  }, [api, limit]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Đang tải hàng việc"
        className="case-skeleton"
        role="status"
      >
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
        <span className="skeleton-line skeleton-line-short" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="state-panel" role="alert">
        <p>{error}</p>
        <button className="button button-secondary" onClick={() => void load()} type="button">
          Thử tải lại
        </button>
      </div>
    );
  }

  if (!collection) {
    return null;
  }

  if (collection.items.length === 0) {
    return (
      <div className="state-panel">
        <h2>Hàng việc trống</h2>
        <p>Không có việc cần xử lý trong phạm vi phân công.</p>
      </div>
    );
  }

  const blocking = collection.items.filter(isBlocking);
  const others = collection.items.filter((item) => !isBlocking(item));

  return (
    <>
      <div className={styles.toolbar}>
        <button className="button button-secondary" onClick={() => void load()} type="button">
          Làm mới
        </button>
      </div>
      {blocking.length > 0 ? (
        <WorkItemGroup heading="Cần xử lý trước" items={blocking} />
      ) : null}
      {others.length > 0 ? (
        <WorkItemGroup heading="Các việc khác" items={others} />
      ) : null}
    </>
  );
}

function WorkItemGroup({ heading, items }: { heading: string; items: WorkItem[] }) {
  return (
    <section aria-label={heading} className={styles.group}>
      <h2 className={styles.groupHeading}>{heading}</h2>
      <ul className={styles.list}>
        {items.map((item, index) => (
          <WorkItemCard item={item} key={`${item.caseId}-${item.kind}-${index}`} />
        ))}
      </ul>
    </section>
  );
}

function WorkItemCard({ item }: { item: WorkItem }) {
  const title = item.titleVi || "Việc cần xử lý";
  const chip = severityChip(item);
  return (
    <li className={styles.card}>
      <article>
        <div className={styles.cardHeader}>
          <span className={styles.caseRef}>
            Hồ sơ {shortCaseId(item.caseId)} · phiên bản {item.caseVersion}
          </span>
          <span
            className={`status-chip status-chip--${chip.variant}`}
            data-severity={item.supported ? item.severity : "UNSUPPORTED"}
          >
            {chip.label}
          </span>
        </div>
        <h3 className={styles.title}>{title}</h3>
        {item.reasonVi ? <p className={styles.reason}>{item.reasonVi}</p> : null}
        <footer className={styles.actions}>
          <Link
            aria-label={`Mở việc — ${title}`}
            className="button button-primary"
            href={item.primaryRoute}
          >
            Mở việc
          </Link>
        </footer>
      </article>
    </li>
  );
}
