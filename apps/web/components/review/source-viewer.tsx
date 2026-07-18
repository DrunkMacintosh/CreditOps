"use client";

import React, { useEffect, useState } from "react";

import { SourceRegionOverlay, type OverlayRegion } from "./source-region-overlay";
import styles from "./document-review.module.css";

export interface SourcePageImage {
  page: number;
  url: string;
}

export interface SourceViewerProps {
  pageCount: number | null;
  regions: OverlayRegion[];
  selectedCandidateId: string | null;
  // No current backend contract provides page image urls; when absent we show a
  // neutral placeholder rather than inventing a fetch.
  pageImages?: SourcePageImage[];
  onSelectRegion?: (candidateId: string) => void;
}

export function SourceViewer({
  pageCount,
  regions,
  selectedCandidateId,
  pageImages,
  onSelectRegion,
}: SourceViewerProps) {
  const firstRegionPage = regions.length > 0 ? regions[0].source.page : 1;
  const totalPages = Math.max(
    pageCount ?? 1,
    ...regions.map((region) => region.source.page),
    1,
  );
  const [currentPage, setCurrentPage] = useState(firstRegionPage);

  // Follow the selected candidate to whichever page its region lives on.
  useEffect(() => {
    if (selectedCandidateId === null) return;
    const selected = regions.find(
      (region) => region.candidateId === selectedCandidateId,
    );
    if (selected) setCurrentPage(selected.source.page);
  }, [selectedCandidateId, regions]);

  function goToPage(page: number) {
    setCurrentPage(Math.min(Math.max(page, 1), totalPages));
  }

  const regionsOnPage = regions.filter(
    (region) => region.source.page === currentPage,
  );
  const pageImage = pageImages?.find((image) => image.page === currentPage);

  return (
    <aside aria-label="Xem vùng nguồn tài liệu" className={styles.viewer}>
      <div className={styles.viewerToolbar}>
        <span aria-live="polite">
          Trang {currentPage}/{totalPages}
        </span>
        {totalPages > 1 ? (
          <span className={styles.pageControls}>
            <button
              className="button button-secondary button-small"
              disabled={currentPage <= 1}
              onClick={() => goToPage(currentPage - 1)}
              type="button"
            >
              Trang trước
            </button>
            <button
              className="button button-secondary button-small"
              disabled={currentPage >= totalPages}
              onClick={() => goToPage(currentPage + 1)}
              type="button"
            >
              Trang sau
            </button>
          </span>
        ) : null}
      </div>
      <div className={styles.page}>
        {pageImage ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            alt={`Trang ${currentPage} của tài liệu`}
            className={styles.pageImage}
            src={pageImage.url}
          />
        ) : (
          <p className={styles.pagePlaceholder}>
            Bản xem trước tài liệu chưa khả dụng.
          </p>
        )}
        <SourceRegionOverlay
          onSelect={(candidateId) => onSelectRegion?.(candidateId)}
          regions={regionsOnPage}
          selectedCandidateId={selectedCandidateId}
        />
      </div>
    </aside>
  );
}
