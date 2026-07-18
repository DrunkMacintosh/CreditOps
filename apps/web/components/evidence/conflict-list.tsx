"use client";

import React from "react";

import type { ConflictDto } from "../../lib/api/contracts";
import { fieldLabelVi } from "../../lib/review/field-labels";
import styles from "./conflict-list.module.css";

// Conflicts never select a winner: no control here may mark a source as
// correct, preferred, or chosen. Resolution is a backend/human-gate flow
// that is not exposed in this workspace.
export function ConflictList({ conflicts }: { conflicts: ConflictDto[] }) {
  return (
    <section aria-labelledby="conflict-list-heading">
      <h2 className={styles.heading} id="conflict-list-heading">
        Mâu thuẫn chứng cứ
      </h2>
      {conflicts.length === 0 ? (
        <p className={styles.empty}>Không phát hiện mâu thuẫn giữa các tài liệu.</p>
      ) : (
        <ul className={styles.list}>
          {conflicts.map((conflict) => (
            <li key={conflict.id}>
              <article
                aria-labelledby={`conflict-${conflict.id}-heading`}
                className={
                  conflict.stale
                    ? `${styles.conflict} ${styles.conflictStale}`
                    : styles.conflict
                }
              >
                <div className={styles.conflictHeader}>
                  <h3 id={`conflict-${conflict.id}-heading`}>
                    {fieldLabelVi(conflict.fieldKey)}
                  </h3>
                  {conflict.stale ? (
                    <span className={styles.badgeStale}>Đã lỗi thời</span>
                  ) : null}
                </div>
                <p className={styles.disagreeLabel}>Các nguồn ghi nhận khác nhau</p>
                {/* Sources are not a semantic list: the conflict itself is
                    the single listitem/article the brief asks for, scoped by
                    `within()` in tests. A nested <ul> here would register
                    extra "listitem" roles and break that scoping. */}
                <div className={styles.sources}>
                  {conflict.sources.map((source, index) => (
                    <div
                      className={styles.source}
                      key={`${conflict.id}-${source.documentVersionId}-${index}`}
                    >
                      <span className={styles.sourceValue}>
                        {formatConflictValue(source.value)}
                      </span>
                      <span className={styles.evidenceChip}>
                        <span aria-hidden="true" className={styles.evidenceDot} />
                        {source.source ? (
                          <span className={styles.sourcePage}>
                            Trang {source.source.page}
                          </span>
                        ) : null}
                        <span className={styles.sourceRef}>
                          {shortDocumentReference(source.documentVersionId)}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
                <p className={styles.note}>
                  Hệ thống không tự chọn giá trị đúng. Mâu thuẫn chờ cán bộ xử
                  lý.
                </p>
              </article>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function formatConflictValue(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "Có" : "Không";
  if (typeof value === "number") return new Intl.NumberFormat("vi-VN").format(value);
  return value;
}

function shortDocumentReference(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}
