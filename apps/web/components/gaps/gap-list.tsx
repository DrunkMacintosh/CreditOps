"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClientError, creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type { CreditCaseDto } from "../../lib/api/contracts";
import type {
  BatchDispositionType,
  GapRequestBatch,
  GapRequestBatchStatus,
  GapRequestItem,
  ItemDisposition,
  RecordDispositionInput,
} from "../../lib/api/gap-requests";
import {
  BATCH_DISPOSITION_TYPE_LABELS,
  BLOCKING_LEVEL_LABELS,
  formatDateTime,
  gapRequestsApi,
  GapRequestsApiClient,
  getGapRequestError,
  isGapRequestBatchNotAvailable,
  ITEM_DISPOSITION_LABELS,
  labelOrUnsupported,
  shortId,
  UNSUPPORTED_ENUM_LABEL,
} from "../../lib/api/gap-requests";
import { CaseNav } from "../shell/case-nav";
import { IntakeCompletionDialog } from "./intake-completion-dialog";
import styles from "./gap-list.module.css";

export type GapRequestItemView = GapRequestItem;

const MAX_RATIONALE = 4000;
const MAX_EDITED_TEXT = 2000;

const GATE_STATUS_LABELS: Record<string, string> = {
  SATISFIED: "Đạt",
  OPEN: "Đang chờ",
};

const ITEM_CHOICES: readonly ItemDisposition[] = ["APPROVED", "REMOVED", "EDITED"];

// GapList: a read-only render of the batch's drafted outbound requests, one per
// open evidence gap. No approve/remove control lives here — a request can only
// be dispositioned through the batch disposition form below, never implicitly.
export function GapList({ items }: { items: GapRequestItemView[] }) {
  return (
    <section aria-labelledby="gap-list-heading">
      <h2 className={styles.heading} id="gap-list-heading">
        Danh sách yêu cầu bổ sung bằng chứng
      </h2>
      {items.length === 0 ? (
        <p className={styles.empty}>
          Không có yêu cầu bổ sung nào: hiện không còn khoảng trống chứng cứ đang mở.
        </p>
      ) : (
        <ul className={styles.list}>
          {items.map((item) => (
            <li className={styles.item} data-blocking={item.blockingLevel} key={item.id}>
              <span className={`${styles.badge} ${blockingBadgeClass(item.blockingLevel)}`}>
                {labelOrUnsupported(BLOCKING_LEVEL_LABELS, item.blockingLevel)}
              </span>
              <p className={styles.issue}>{item.requestText}</p>
              <p className={styles.missingInformation}>
                Khoảng trống nguồn: {shortId(item.gapId)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function blockingBadgeClass(level: string): string {
  switch (level) {
    case "BLOCKING":
      return styles.badgeBlocking;
    case "CONDITIONAL":
      return styles.badgeConditional;
    case "CLARIFICATION":
      return styles.badgeClarification;
    default:
      return styles.badgeStale;
  }
}

function gateStatusLabel(status: string): string {
  return GATE_STATUS_LABELS[status] ?? UNSUPPORTED_ENUM_LABEL;
}

// Append-only human disposition of one gap-request batch. Type is NEVER
// preselected; the option set depends on whether the batch has drafted items;
// APPROVED_WITH_CHANGES requires an explicit per-item choice (and replacement
// text for every item marked EDITED). The server re-derives the gate; this form
// only records and, on a 409, keeps the draft and prompts a reload.
function BatchDispositionForm({
  batch,
  onSubmit,
  onReload,
}: {
  batch: GapRequestBatch;
  onSubmit: (input: RecordDispositionInput) => Promise<void>;
  onReload: () => void;
}) {
  const hasItems = batch.items.length > 0;
  // NO_OUTBOUND_REQUESTS is offered ONLY for an empty batch; APPROVED_ALL /
  // APPROVED_WITH_CHANGES only for a batch with drafted items. REJECTED always.
  const options: readonly BatchDispositionType[] = hasItems
    ? ["APPROVED_ALL", "APPROVED_WITH_CHANGES", "REJECTED"]
    : ["NO_OUTBOUND_REQUESTS", "REJECTED"];

  const [type, setType] = useState<BatchDispositionType | null>(null);
  const [rationale, setRationale] = useState("");
  const [itemChoices, setItemChoices] = useState<Record<string, ItemDisposition>>({});
  const [editedTexts, setEditedTexts] = useState<Record<string, string>>({});
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const perItem = type === "APPROVED_WITH_CHANGES";

  function buildInput(): RecordDispositionInput | { error: string } {
    if (!type) return { error: "Chọn một loại quyết định trước khi ghi." };
    const note = rationale.trim();
    if (note.length === 0) {
      return { error: "Nhập lý do cho quyết định; đây là trường bắt buộc." };
    }
    if (type !== "APPROVED_WITH_CHANGES") {
      return { dispositionType: type, rationale: note };
    }
    const itemDispositions: Record<string, ItemDisposition> = {};
    const editedForItems: Record<string, string> = {};
    for (const item of batch.items) {
      const choice = itemChoices[item.id];
      if (!choice) {
        return { error: "Chọn cách xử lý cho từng mục yêu cầu bổ sung." };
      }
      itemDispositions[item.id] = choice;
      if (choice === "EDITED") {
        const text = (editedTexts[item.id] ?? "").trim();
        if (text.length === 0) {
          return { error: "Nhập nội dung chỉnh sửa cho mỗi mục được đánh dấu chỉnh sửa." };
        }
        editedForItems[item.id] = text;
      }
    }
    return {
      dispositionType: type,
      rationale: note,
      itemDispositions,
      editedTexts: editedForItems,
    };
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);

    const built = buildInput();
    if ("error" in built) {
      setFieldError(built.error);
      return;
    }
    setFieldError(null);

    setPending(true);
    try {
      await onSubmit(built);
      // Success: the parent refetches and this form unmounts / resets.
      setType(null);
      setRationale("");
      setItemChoices({});
      setEditedTexts({});
    } catch (requestError) {
      // House rule: a 409 keeps the draft intact and prompts a reload rather
      // than discarding the officer's work.
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getGapRequestError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <p className={styles.formHeading}>Ghi quyết định cho đợt yêu cầu bổ sung</p>
      <p className={styles.formHint}>
        Mỗi yêu cầu là bản nháp; hệ thống không gửi đi bất cứ đâu. Cán bộ tiếp nhận ghi một
        quyết định cho cả đợt. Cổng chỉ đạt khi quyết định còn khớp với khoảng trống hiện tại.
      </p>

      <fieldset className={styles.fieldset}>
        <legend className={styles.fieldsetLegend}>
          Loại quyết định <span className={styles.required}>*</span>
        </legend>
        <div className={styles.radioGroup}>
          {options.map((option) => (
            <label
              className={styles.radioOption}
              data-checked={type === option ? "true" : "false"}
              key={option}
            >
              <input
                checked={type === option}
                disabled={pending}
                name="gap-disposition-type"
                onChange={() => {
                  setType(option);
                  setFieldError(null);
                }}
                type="radio"
                value={option}
              />
              <span>{BATCH_DISPOSITION_TYPE_LABELS[option]}</span>
            </label>
          ))}
        </div>
      </fieldset>

      {perItem ? (
        <div className={styles.itemChoiceList}>
          <p className={styles.itemChoiceLead}>Xử lý từng mục yêu cầu bổ sung</p>
          {batch.items.map((item) => {
            const choice = itemChoices[item.id];
            return (
              <div className={styles.itemChoice} key={item.id}>
                <p className={styles.itemChoiceText}>{item.requestText}</p>
                <div className={styles.radioGroup} role="group" aria-label="Cách xử lý mục">
                  {ITEM_CHOICES.map((option) => (
                    <label
                      className={styles.radioOption}
                      data-checked={choice === option ? "true" : "false"}
                      key={option}
                    >
                      <input
                        checked={choice === option}
                        disabled={pending}
                        name={`item-choice-${item.id}`}
                        onChange={() => {
                          setItemChoices((current) => ({ ...current, [item.id]: option }));
                          setFieldError(null);
                        }}
                        type="radio"
                        value={option}
                      />
                      <span>{ITEM_DISPOSITION_LABELS[option]}</span>
                    </label>
                  ))}
                </div>
                {choice === "EDITED" ? (
                  <label className={styles.editedField}>
                    <span className={styles.fieldLabel}>
                      Nội dung chỉnh sửa <span className={styles.required}>*</span>
                    </span>
                    <textarea
                      className={styles.textarea}
                      disabled={pending}
                      maxLength={MAX_EDITED_TEXT}
                      onChange={(changeEvent) => {
                        const text = changeEvent.target.value;
                        setEditedTexts((current) => ({ ...current, [item.id]: text }));
                        if (fieldError) setFieldError(null);
                      }}
                      value={editedTexts[item.id] ?? ""}
                    />
                  </label>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="gap-disposition-rationale">
          Lý do quyết định <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id="gap-disposition-rationale"
          maxLength={MAX_RATIONALE}
          onChange={(event) => {
            setRationale(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={rationale}
        />
        <span className={styles.charCount}>
          {rationale.length}/{MAX_RATIONALE}
        </span>
      </div>

      {fieldError ? (
        <p className={styles.fieldError} role="alert">
          {fieldError}
        </p>
      ) : null}

      {submitError ? (
        <div className={styles.submitError} role="alert">
          <p>{submitError}</p>
          {staleReload ? (
            <button
              className="button button-secondary"
              onClick={() => onReload()}
              type="button"
            >
              Tải lại danh sách
            </button>
          ) : null}
        </div>
      ) : null}

      <div className={styles.formActions}>
        <button aria-busy={pending} className={styles.submit} disabled={pending} type="submit">
          {pending ? "Đang ghi quyết định…" : "Duyệt nội dung yêu cầu bổ sung"}
        </button>
      </div>
    </form>
  );
}

// Client loader for app/ho-so/[caseId]/khoang-trong/page.tsx. Loads the case
// (version + canCompleteIntake) and, if one exists, the current gap-request
// batch via GET. It NEVER assembles a batch on render — assemble-or-get runs
// only on the explicit "Tạo/tải danh sách yêu cầu bổ sung" action.
export function GapWorkspace({
  caseId,
  api = creditOpsApi,
  gapApi = gapRequestsApi,
}: {
  caseId: string;
  api?: Pick<typeof creditOpsApi, "getCase" | "completeIntake">;
  gapApi?: Pick<GapRequestsApiClient, "getBatch" | "assembleBatch" | "recordDisposition">;
}) {
  const [creditCase, setCreditCase] = useState<CreditCaseDto | null>(null);
  const [batchStatus, setBatchStatus] = useState<GapRequestBatchStatus | null>(null);
  const [batchNotAvailable, setBatchNotAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [assembling, setAssembling] = useState(false);
  const [assembleError, setAssembleError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setBatchNotAvailable(false);
    setRefreshError(null);
    try {
      const loadedCase = await api.getCase(caseId);
      setCreditCase(loadedCase);
      try {
        setBatchStatus(await gapApi.getBatch(caseId));
      } catch (batchError) {
        if (isGapRequestBatchNotAvailable(batchError)) {
          setBatchStatus(null);
          setBatchNotAvailable(true);
        } else {
          throw batchError;
        }
      }
    } catch (requestError) {
      setError(getVietnameseApiError(requestError));
    } finally {
      setLoading(false);
    }
  }, [api, caseId, gapApi]);

  useEffect(() => {
    void load();
  }, [load]);

  // Refetch the batch after a write; never throws — a recorded disposition must
  // not look failed because a follow-up read hiccuped.
  const refreshBatch = useCallback(async () => {
    setRefreshError(null);
    setBatchNotAvailable(false);
    try {
      setBatchStatus(await gapApi.getBatch(caseId));
    } catch (requestError) {
      if (isGapRequestBatchNotAvailable(requestError)) {
        setBatchStatus(null);
        setBatchNotAvailable(true);
      } else {
        setRefreshError(getGapRequestError(requestError));
      }
    }
  }, [caseId, gapApi]);

  // Explicit user action only — assemble-or-get, then refetch the batch view.
  const assemble = useCallback(async () => {
    setAssembling(true);
    setAssembleError(null);
    try {
      await gapApi.assembleBatch(caseId);
      await refreshBatch();
    } catch (requestError) {
      setAssembleError(getGapRequestError(requestError));
    } finally {
      setAssembling(false);
    }
  }, [caseId, gapApi, refreshBatch]);

  const recordDisposition = useCallback(
    async (batchId: string, input: RecordDispositionInput) => {
      await gapApi.recordDisposition(caseId, batchId, input);
      await refreshBatch();
    },
    [caseId, gapApi, refreshBatch],
  );

  const openGapCount = useMemo(
    () => batchStatus?.batch.items.length ?? 0,
    [batchStatus],
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

  const canCompleteIntake = creditCase.capabilities.canCompleteIntake;
  const assembleLabel = assembling
    ? "Đang tạo danh sách…"
    : "Tạo/tải danh sách yêu cầu bổ sung";

  return (
    <>
      <CaseNav caseId={caseId} current="khoang-trong" />
      <div className="page-heading">
        <p className="eyebrow">Hồ sơ · phiên bản {creditCase.version}</p>
        <h1>Khoảng trống chứng cứ</h1>
      </div>

      {batchStatus ? (
        <div className={styles.batchArea}>
          <div className={styles.gateRow}>
            <span
              className={`${styles.gateChip} ${
                batchStatus.gateStatus === "SATISFIED" ? styles.gateChipOk : styles.gateChipWait
              }`}
            >
              Cổng yêu cầu bổ sung (G2): {gateStatusLabel(batchStatus.gateStatus)}
            </span>
            <span className={styles.gateMeta}>
              Phiên bản đợt: v{batchStatus.batch.caseVersion} · Mã đợt{" "}
              {shortId(batchStatus.batch.batchId)}
            </span>
          </div>

          {batchStatus.stale ? (
            <div className={styles.staleBanner} role="alert">
              <span className={styles.staleBadge}>Đã cũ</span>
              <p>Danh sách đã cũ so với khoảng trống hiện tại.</p>
              <button
                className="button button-secondary"
                disabled={assembling}
                onClick={() => void assemble()}
                type="button"
              >
                {assembling ? "Đang tạo lại…" : "Tạo lại danh sách"}
              </button>
            </div>
          ) : null}

          {assembleError ? (
            <div className="state-panel" role="alert">
              <p>{assembleError}</p>
            </div>
          ) : null}

          <GapList items={batchStatus.batch.items} />

          {batchStatus.dispositions.length > 0 ? (
            <section aria-label="Lịch sử quyết định" className={styles.history}>
              <p className={styles.historyHeading}>Quyết định đã ghi</p>
              {batchStatus.dispositions.map((disposition) => (
                <div className={styles.dispoItem} key={disposition.id}>
                  <div className={styles.dispoHead}>
                    <span className={styles.dispoType}>
                      {labelOrUnsupported(
                        BATCH_DISPOSITION_TYPE_LABELS,
                        disposition.dispositionType,
                      )}
                    </span>
                    <span className={styles.dispoMeta}>
                      {disposition.actorRole} · {formatDateTime(disposition.createdAt)}
                    </span>
                  </div>
                  <p className={styles.dispoNote}>{disposition.rationale}</p>
                </div>
              ))}
            </section>
          ) : null}

          {refreshError ? (
            <div className="state-panel" role="alert">
              <p>Đã ghi vào sổ, nhưng không tải lại được bản mới nhất: {refreshError}</p>
              <button
                className="button button-secondary"
                onClick={() => void refreshBatch()}
                type="button"
              >
                Tải lại
              </button>
            </div>
          ) : null}

          {batchStatus.stale ? (
            <p className={styles.staleFormNote}>
              Hãy tạo lại danh sách trước khi ghi quyết định: một quyết định trên danh sách đã
              cũ sẽ không làm cổng đạt.
            </p>
          ) : (
            <BatchDispositionForm
              batch={batchStatus.batch}
              onReload={() => void refreshBatch()}
              onSubmit={(input) => recordDisposition(batchStatus.batch.batchId, input)}
            />
          )}
        </div>
      ) : (
        <div className={styles.assemblePanel}>
          <p className={styles.assembleLead}>
            {batchNotAvailable
              ? "Chưa có danh sách yêu cầu bổ sung cho phiên bản hồ sơ này."
              : "Không thể đọc danh sách yêu cầu bổ sung."}
          </p>
          <p className={styles.assembleBody}>
            Danh sách được lắp ráp tất định từ các khoảng trống chứng cứ đang mở khi cán bộ tiếp
            nhận yêu cầu. Hệ thống không tự tạo danh sách khi mở trang.
          </p>
          <button
            aria-busy={assembling}
            className="button button-primary"
            disabled={assembling}
            onClick={() => void assemble()}
            type="button"
          >
            {assembleLabel}
          </button>
          {assembleError ? (
            <div className="state-panel" role="alert">
              <p>{assembleError}</p>
            </div>
          ) : null}
        </div>
      )}

      {canCompleteIntake && (
        <div className={styles.completionArea}>
          <button
            className="button button-primary"
            onClick={() => setDialogOpen(true)}
            type="button"
          >
            Hoàn tất tiếp nhận…
          </button>
          <IntakeCompletionDialog
            canCompleteIntake={canCompleteIntake}
            caseId={caseId}
            caseVersion={creditCase.version}
            onClose={() => setDialogOpen(false)}
            onComplete={() => api.completeIntake(caseId)}
            onCompleted={() => {
              void load();
            }}
            open={dialogOpen}
            openGapCount={openGapCount}
          />
        </div>
      )}
    </>
  );
}
