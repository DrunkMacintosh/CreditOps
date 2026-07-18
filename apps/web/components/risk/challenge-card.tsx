import React from "react";

import type {
  Challenge,
  Disposition,
  RecordDispositionInput,
} from "../../lib/api/risk-review";
import {
  CHALLENGE_TYPE_LABELS,
  CONFIDENCE_LABELS,
  DISPOSITION_TYPE_LABELS,
  RAISED_BY_LABELS,
  SEVERITY_LABELS,
  isSevere,
  labelFor,
} from "../../lib/api/risk-review";
import { DispositionForm } from "./disposition-form";
import { EvidenceChip, citationChip, targetChip } from "./evidence-chip";
import styles from "./risk-review.module.css";

function severityChipClass(severity: string): string {
  if (severity === "CRITICAL" || severity === "HIGH") return styles.chipRisk;
  if (severity === "MEDIUM") return styles.chipAmber;
  return styles.chipInfo;
}

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}

function RecordedDisposition({ disposition }: { disposition: Disposition }) {
  return (
    <div className={styles.dispoItem}>
      <div className={styles.dispoHead}>
        <span className={styles.dispoType}>
          {labelFor(DISPOSITION_TYPE_LABELS, disposition.dispositionType)}
        </span>
        <span className={styles.dispoMeta}>
          {disposition.actorRole} · {formatDateTime(disposition.createdAt)}
        </span>
      </div>
      <p className={styles.dispoNote}>{disposition.rationale}</p>
    </div>
  );
}

export function ChallengeCard({
  challenge,
  onRecord,
}: {
  challenge: Challenge;
  // Records a per-challenge disposition then refreshes; throws on failure.
  onRecord: (input: RecordDispositionInput) => Promise<void>;
}) {
  const severe = isSevere(challenge.severity);
  const disposed = challenge.dispositions.length > 0;
  const target = targetChip(challenge.target);

  return (
    <li>
      <article
        aria-label={`Thách thức: ${labelFor(CHALLENGE_TYPE_LABELS, challenge.challengeType)}`}
        className={styles.challenge}
        data-severity={challenge.severity}
      >
        <div className={styles.challengeTop}>
          <span className={styles.challengeType}>
            {labelFor(CHALLENGE_TYPE_LABELS, challenge.challengeType)}
          </span>
          <span className={`${styles.chip} ${severityChipClass(challenge.severity)}`}>
            {challenge.severity === "CRITICAL" ? (
              <span aria-hidden="true" className={styles.critMark}>
                ▲
              </span>
            ) : null}
            Mức {labelFor(SEVERITY_LABELS, challenge.severity)}
          </span>
          <span className={`${styles.chip} ${styles.chipMuted}`}>
            {labelFor(RAISED_BY_LABELS, challenge.raisedBy)}
          </span>
          <span className={styles.confidence}>
            Độ tin cậy: {labelFor(CONFIDENCE_LABELS, challenge.confidence)}
          </span>
        </div>

        <p className={styles.statement}>{challenge.statement}</p>

        <div className={styles.evidenceBlock}>
          <p className={styles.evidenceLabel}>Chứng cứ</p>
          <div className={styles.chipRow}>
            <EvidenceChip
              kind={target.kind}
              reference={target.reference}
              title={target.title}
              variant="target"
            />
            {challenge.citations.map((citation, index) => {
              const chip = citationChip(citation);
              return (
                <EvidenceChip
                  key={`${chip.kind}-${chip.reference}-${index}`}
                  kind={chip.kind}
                  reference={chip.reference}
                  title={chip.title}
                />
              );
            })}
          </div>
        </div>

        {disposed ? (
          <div className={styles.dispositions}>
            <p className={styles.dispositionsHeading}>Quyết định đã ghi</p>
            {challenge.dispositions.map((disposition) => (
              <RecordedDisposition disposition={disposition} key={disposition.id} />
            ))}
          </div>
        ) : (
          <DispositionForm
            heading="Ghi quyết định cho thách thức này"
            hint={
              severe
                ? "Thách thức mức Cao/Nghiêm trọng cần có quyết định của cán bộ trước khi cổng G3 đạt. Quyết định được ghi thêm và không thể sửa hay xóa."
                : "Cán bộ ghi lại cách xử lý thách thức này. Quyết định được ghi thêm và không thể sửa hay xóa."
            }
            onSubmit={onRecord}
            rationaleLabel="Lý do quyết định"
            submitLabel="Ghi quyết định"
          />
        )}
      </article>
    </li>
  );
}
