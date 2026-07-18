"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiClientError, creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type {
  CandidateDispositionDto,
  CreditCaseDto,
  CreditOpsApi,
  DocumentReviewDto,
  DocumentStage,
} from "../../lib/api/contracts";
import { CaseNav } from "../shell/case-nav";
import {
  CandidateDispositionForm,
  type CandidateDraft,
} from "./candidate-disposition-form";
import { SourceViewer } from "./source-viewer";
import styles from "./document-review.module.css";

const STAGE_LABELS_VI: Record<DocumentStage, string> = {
  REGISTERED: "Đã đăng ký",
  SECURITY_VALIDATED: "Đã kiểm tra an toàn",
  PARSED: "Đã bóc tách",
  CLASSIFIED: "Đã phân loại",
  EXTRACTED: "Đã trích xuất",
  INDEXED: "Đã lập chỉ mục",
  READY_FOR_OFFICER_REVIEW: "Sẵn sàng để cán bộ rà soát",
};

function emptyDraft(): CandidateDraft {
  return { disposition: null, correctedValue: "", rationale: "" };
}

function isResolved(draft: CandidateDraft | undefined): boolean {
  if (!draft || draft.disposition === null) return false;
  if (draft.disposition === "CORRECTED") {
    return (
      draft.correctedValue.trim().length > 0 && draft.rationale.trim().length > 0
    );
  }
  return true;
}

function toDisposition(
  candidateId: string,
  draft: CandidateDraft,
): CandidateDispositionDto | null {
  if (draft.disposition === null) return null;
  if (draft.disposition === "CORRECTED") {
    return {
      candidateId,
      disposition: "CORRECTED",
      correctedValue: draft.correctedValue,
      rationale: draft.rationale,
    };
  }
  return { candidateId, disposition: draft.disposition };
}

export interface DocumentReviewProps {
  review: DocumentReviewDto;
  // Carried for navigation/messages by the page loader; not required by the
  // pure review surface (the mandated test renders without it).
  caseId?: string;
  canConfirm?: boolean;
  api?: Pick<CreditOpsApi, "confirmDocument">;
  onConfirmed?: () => void;
  onReloadRequired?: () => void;
}

export function DocumentReview({
  review,
  canConfirm = false,
  api = creditOpsApi,
  onConfirmed,
  onReloadRequired,
}: DocumentReviewProps) {
  const candidates = review.candidates;
  const [drafts, setDrafts] = useState<Record<string, CandidateDraft>>(() =>
    Object.fromEntries(candidates.map((candidate) => [candidate.id, emptyDraft()])),
  );
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showReload, setShowReload] = useState(false);
  const fieldsetRefs = useRef<Record<string, HTMLFieldSetElement | null>>({});

  useEffect(() => {
    const firstUnresolved = candidates.find(
      (candidate) => !isResolved(drafts[candidate.id]),
    );
    if (firstUnresolved) {
      fieldsetRefs.current[firstUnresolved.id]?.focus();
    }
    // Focus the first unresolved candidate once, on mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const regions = useMemo(
    () =>
      candidates.map((candidate) => ({
        candidateId: candidate.id,
        fieldKey: candidate.fieldKey,
        source: candidate.source,
      })),
    [candidates],
  );

  const updateDraft = useCallback(
    (candidateId: string, patch: Partial<CandidateDraft>) => {
      setDrafts((current) => ({
        ...current,
        [candidateId]: { ...(current[candidateId] ?? emptyDraft()), ...patch },
      }));
    },
    [],
  );

  const allResolved =
    candidates.length > 0 &&
    candidates.every((candidate) => isResolved(drafts[candidate.id]));
  const confirmDisabled = !canConfirm || !allResolved || submitting;
  const stageLabel = STAGE_LABELS_VI[review.stage];

  const handleConfirm = useCallback(async () => {
    if (!canConfirm || !allResolved || submitting) return;
    const dispositions = candidates
      .map((candidate) => toDisposition(candidate.id, drafts[candidate.id]))
      .filter(
        (disposition): disposition is CandidateDispositionDto =>
          disposition !== null,
      );
    setSubmitting(true);
    setError(null);
    setShowReload(false);
    try {
      await api.confirmDocument(review.documentId, {
        expectedDocumentVersion: review.documentVersion,
        dispositions,
      });
      onConfirmed?.();
    } catch (requestError) {
      setError(getVietnameseApiError(requestError));
      setShowReload(
        requestError instanceof ApiClientError && requestError.status === 409,
      );
    } finally {
      setSubmitting(false);
    }
  }, [
    allResolved,
    api,
    canConfirm,
    candidates,
    drafts,
    onConfirmed,
    review.documentId,
    review.documentVersion,
    submitting,
  ]);

  return (
    <section aria-labelledby="document-review-heading" className={styles.review}>
      <div className={styles.reviewHeader}>
        <h2 id="document-review-heading">Chứng cứ đề xuất từ tài liệu</h2>
        <p className={styles.stage}>Giai đoạn tài liệu: {stageLabel}</p>
        {review.fileName ? (
          <p className={styles.fileName}>Tài liệu: {review.fileName}</p>
        ) : null}
        <p className={styles.boundaryNote}>
          Mỗi giá trị dưới đây là dữ liệu trích xuất cần cán bộ xử lý trước khi
          xác nhận. Đây không phải quyết định tín dụng.
        </p>
      </div>

      <div className={styles.workspace}>
        <div className={styles.candidates}>
          {candidates.length === 0 ? (
            <div className="state-panel">
              <p>Chưa có dữ liệu trích xuất cho tài liệu này.</p>
              <p>Giai đoạn tài liệu: {stageLabel}</p>
            </div>
          ) : (
            candidates.map((candidate) => (
              <CandidateDispositionForm
                candidate={candidate}
                disabled={!canConfirm}
                draft={drafts[candidate.id] ?? emptyDraft()}
                fieldsetRef={(element) => {
                  fieldsetRefs.current[candidate.id] = element;
                }}
                key={candidate.id}
                onChange={(patch) => updateDraft(candidate.id, patch)}
                onSelect={() => setSelectedCandidateId(candidate.id)}
                selected={selectedCandidateId === candidate.id}
              />
            ))
          )}

          {!canConfirm && candidates.length > 0 ? (
            <p className="permission-note">
              Bạn không có quyền xác nhận tài liệu này.
            </p>
          ) : null}

          {candidates.length === 0 ? (
            <p className="permission-note">
              Không có dữ liệu để xác nhận. Nút xác nhận sẽ mở lại khi có chứng cứ
              trích xuất.
            </p>
          ) : null}

          {error ? (
            <div className="state-panel" role="alert">
              <p>{error}</p>
              {showReload ? (
                <button
                  className="button button-secondary"
                  onClick={() => onReloadRequired?.()}
                  type="button"
                >
                  Tải lại phiên bản mới
                </button>
              ) : null}
            </div>
          ) : null}

          <button
            aria-busy={submitting}
            className="button button-primary"
            disabled={confirmDisabled}
            onClick={() => void handleConfirm()}
            type="button"
          >
            Xác nhận tài liệu
          </button>
        </div>

        <SourceViewer
          onSelectRegion={setSelectedCandidateId}
          pageCount={review.pageCount}
          regions={regions}
          selectedCandidateId={selectedCandidateId}
        />
      </div>
    </section>
  );
}

interface DocumentReviewLoaderProps {
  caseId: string;
  documentId: string;
  api?: Pick<CreditOpsApi, "getCase" | "getDocumentReview">;
}

// Client loader rendered by the server page: loads the case (for the confirm
// capability) and the document review in parallel, then hands data to
// DocumentReview via props. Fails closed with the shared Vietnamese error idiom
// until backend Task 8 ships the review endpoint.
export function DocumentReviewLoader({
  caseId,
  documentId,
  api = creditOpsApi,
}: DocumentReviewLoaderProps) {
  const [creditCase, setCreditCase] = useState<CreditCaseDto | null>(null);
  const [review, setReview] = useState<DocumentReviewDto | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmedNote, setConfirmedNote] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [caseResult, reviewResult] = await Promise.all([
        api.getCase(caseId),
        api.getDocumentReview(documentId),
      ]);
      setCreditCase(caseResult);
      setReview(reviewResult);
    } catch (requestError) {
      setError(getVietnameseApiError(requestError));
    } finally {
      setLoading(false);
    }
  }, [api, caseId, documentId]);

  const reloadReview = useCallback(async () => {
    try {
      setReview(await api.getDocumentReview(documentId));
    } catch (requestError) {
      setError(getVietnameseApiError(requestError));
    }
  }, [api, documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Đang tải dữ liệu tài liệu"
        className="case-skeleton"
        role="status"
      >
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (error || !creditCase || !review) {
    return (
      <div className="state-panel" role="alert">
        <p>{error ?? "Không thể đọc dữ liệu tài liệu."}</p>
        <button
          className="button button-secondary"
          onClick={() => void load()}
          type="button"
        >
          Thử tải lại
        </button>
      </div>
    );
  }

  return (
    <>
      <CaseNav caseId={caseId} current="tai-lieu" />
      <div className="page-heading">
        <p className="eyebrow">Hồ sơ · phiên bản {creditCase.version}</p>
        <h1>Xác nhận dữ liệu tài liệu</h1>
        <p>
          Cán bộ xử lý từng chứng cứ trích xuất trước khi xác nhận tài liệu. Đây
          không phải quyết định tín dụng.
        </p>
      </div>
      {confirmedNote ? (
        <p className="permission-note" role="status">
          Đã xác nhận tài liệu.
        </p>
      ) : null}
      <DocumentReview
        canConfirm={creditCase.capabilities.canConfirm}
        caseId={caseId}
        key={`${review.documentId}:${review.documentVersion}`}
        onConfirmed={() => {
          setConfirmedNote(true);
          void reloadReview();
        }}
        onReloadRequired={() => {
          setConfirmedNote(false);
          void reloadReview();
        }}
        review={review}
      />
    </>
  );
}
