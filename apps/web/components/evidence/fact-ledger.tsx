"use client";

import React, { useCallback, useEffect, useState } from "react";

import { creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type {
  ConfirmedFactDto,
  ConflictDto,
  CreditCaseDto,
  CreditOpsApi,
} from "../../lib/api/contracts";
import { fieldLabelVi } from "../../lib/review/field-labels";
import { CaseNav } from "../shell/case-nav";
import { ConflictList } from "./conflict-list";
import styles from "./fact-ledger.module.css";

// A confirmed fact is never hidden or dropped, even when stale or corrected:
// the correction lineage (original candidate value) stays visible alongside
// the confirmed value.
export function FactLedger({ facts }: { facts: ConfirmedFactDto[] }) {
  if (facts.length === 0) {
    return (
      <section aria-labelledby="fact-ledger-heading" className={styles.section}>
        <h2 className={styles.emptyHeading} id="fact-ledger-heading">
          Sổ cái dữ kiện đã xác nhận
        </h2>
        <p>Chưa có dữ kiện nào được xác nhận.</p>
      </section>
    );
  }

  return (
    <section aria-label="Sổ cái dữ kiện đã xác nhận" className={styles.section}>
      <div className={styles.tableScroll}>
        <table className={styles.table}>
          <caption>Sổ cái dữ kiện đã xác nhận</caption>
          <thead>
            <tr>
              <th scope="col">Trường thông tin</th>
              <th scope="col">Giá trị đã xác nhận</th>
              <th scope="col">Giá trị trích xuất gốc</th>
              <th scope="col">Nguồn</th>
              <th scope="col">Thời điểm xác nhận</th>
              <th scope="col">Trạng thái</th>
            </tr>
          </thead>
          <tbody>
            {facts.map((fact) => {
              const corrected = fact.value !== fact.candidateValue;
              return (
                <tr className={fact.stale ? styles.rowStale : undefined} key={fact.id}>
                  <th scope="row">{fieldLabelVi(fact.fieldKey)}</th>
                  <td>
                    <span className={styles.value}>{formatFactValue(fact.value)}</span>
                  </td>
                  <td>
                    {corrected ? (
                      <span className={styles.candidateValue}>
                        {formatFactValue(fact.candidateValue)}
                      </span>
                    ) : null}
                  </td>
                  <td>
                    <span className={styles.evidenceChip}>
                      <span aria-hidden="true" className={styles.evidenceDot} />
                      <span className={styles.evidencePage}>Trang {fact.source.page}</span>
                      <span className={styles.evidenceRef}>
                        {shortDocumentReference(fact.documentVersionId)}
                      </span>
                    </span>
                  </td>
                  <td>
                    <span className={styles.confirmedAt}>
                      {formatConfirmedAt(fact.confirmedAt)}
                    </span>
                  </td>
                  <td>
                    {fact.stale ? (
                      <span className={styles.badgeStale}>Đã lỗi thời</span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function shortDocumentReference(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

function formatFactValue(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "Có" : "Không";
  if (typeof value === "number") return new Intl.NumberFormat("vi-VN").format(value);
  return value;
}

function formatConfirmedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}

// The đối chiếu (evidence reconciliation) page loader: fetches the case, the
// confirmed-fact ledger, and the conflict list in parallel. A failure in one
// never hides the sections that loaded — it shows an inline retry panel that
// refetches only that section, and never fabricates the missing data.
export function EvidenceDashboard({
  api = creditOpsApi,
  caseId,
}: {
  api?: Pick<CreditOpsApi, "getCase" | "listEvidence" | "listConflicts">;
  caseId: string;
}) {
  const [creditCase, setCreditCase] = useState<CreditCaseDto | null>(null);
  const [caseError, setCaseError] = useState<string | null>(null);
  const [facts, setFacts] = useState<ConfirmedFactDto[] | null>(null);
  const [factsError, setFactsError] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<ConflictDto[] | null>(null);
  const [conflictsError, setConflictsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadCase = useCallback(async () => {
    setCaseError(null);
    try {
      setCreditCase(await api.getCase(caseId));
    } catch (requestError) {
      setCaseError(getVietnameseApiError(requestError));
    }
  }, [api, caseId]);

  const loadEvidence = useCallback(async () => {
    setFactsError(null);
    try {
      const evidence = await api.listEvidence(caseId);
      setFacts(evidence.items);
    } catch (requestError) {
      setFactsError(getVietnameseApiError(requestError));
    }
  }, [api, caseId]);

  const loadConflicts = useCallback(async () => {
    setConflictsError(null);
    try {
      const conflictList = await api.listConflicts(caseId);
      setConflicts(conflictList.items);
    } catch (requestError) {
      setConflictsError(getVietnameseApiError(requestError));
    }
  }, [api, caseId]);

  useEffect(() => {
    let active = true;
    void (async () => {
      setLoading(true);
      await Promise.all([loadCase(), loadEvidence(), loadConflicts()]);
      if (active) setLoading(false);
    })();
    return () => {
      active = false;
    };
  }, [loadCase, loadEvidence, loadConflicts]);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Đang tải đối chiếu chứng cứ"
        className="case-skeleton"
        role="status"
      >
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (caseError || !creditCase) {
    return (
      <div className="state-panel" role="alert">
        <p>{caseError ?? "Không thể đọc hồ sơ."}</p>
        <button className="button button-secondary" onClick={() => void loadCase()} type="button">
          Thử tải lại
        </button>
      </div>
    );
  }

  return (
    <>
      <CaseNav caseId={caseId} current="doi-chieu" />
      <div className="page-heading">
        <p className="eyebrow">Hồ sơ · phiên bản {creditCase.version}</p>
        <h1>Đối chiếu chứng cứ</h1>
      </div>
      {factsError ? (
        <div className="state-panel" role="alert">
          <p>{factsError}</p>
          <button
            className="button button-secondary"
            onClick={() => void loadEvidence()}
            type="button"
          >
            Thử tải lại
          </button>
        </div>
      ) : (
        <FactLedger facts={facts ?? []} />
      )}
      {conflictsError ? (
        <div className="state-panel" role="alert">
          <p>{conflictsError}</p>
          <button
            className="button button-secondary"
            onClick={() => void loadConflicts()}
            type="button"
          >
            Thử tải lại
          </button>
        </div>
      ) : (
        <ConflictList conflicts={conflicts ?? []} />
      )}
    </>
  );
}
