"use client";

import React from "react";

import type { PageRegionDto } from "../../lib/api/contracts";
import { fieldLabelVi } from "../../lib/review/field-labels";
import styles from "./document-review.module.css";

export interface OverlayRegion {
  candidateId: string;
  fieldKey: string;
  source: PageRegionDto;
  // 1-based marker matching the numbered field card, so the officer can pair
  // an extracted field with its highlighted region at a glance.
  index?: number;
}

interface SourceRegionOverlayProps {
  regions: OverlayRegion[];
  selectedCandidateId: string | null;
  onSelect?: (candidateId: string) => void;
}

// Absolutely-positioned rectangles derived from normalized (0..1) coordinates.
// The parent .page box carries the fixed A4 aspect ratio, so percentages map
// directly onto it. Selection is a pure view concern (never a confirm action).
export function SourceRegionOverlay({
  regions,
  selectedCandidateId,
  onSelect,
}: SourceRegionOverlayProps) {
  return (
    <div className={styles.overlay}>
      {regions.map((region) => {
        const selected = region.candidateId === selectedCandidateId;
        return (
          <button
            aria-label={`Vùng nguồn ${fieldLabelVi(region.fieldKey)}, trang ${region.source.page}`}
            aria-pressed={selected}
            className={styles.region}
            data-selected={selected ? "true" : "false"}
            key={region.candidateId}
            onClick={() => onSelect?.(region.candidateId)}
            style={{
              left: `${region.source.x * 100}%`,
              top: `${region.source.y * 100}%`,
              width: `${region.source.width * 100}%`,
              height: `${region.source.height * 100}%`,
            }}
            type="button"
          >
            {region.index !== undefined ? (
              <span aria-hidden="true" className={styles.regionIndex}>
                {region.index}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
