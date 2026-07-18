"use client";

import React, { useCallback, useEffect, useId, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  allowedItemTransitions,
  ASSET_KIND_LABELS,
  derivePerfectionBlockers,
  formatDate,
  formatDateTime,
  getSecurityError,
  ITEM_TRANSITION_LABELS,
  labelOrUnsupported,
  PERFECTION_STATUS_LABELS,
  SECURITY_ASSET_KINDS,
  SecurityInterestsApiClient,
  securityInterestsApi,
  shortId,
  type AddItemInput,
  type CreateInterestInput,
  type InterestWithItems,
  type PerfectionItem,
  type PerfectionStatus,
  type SecurityLedger,
  type TransitionItemInput,
} from "../../lib/api/security-interests";
import { RecordActionForm } from "../gates/record-action-form";
import { CaseNav } from "../shell/case-nav";
import styles from "../gates/gates.module.css";

type SecurityApi = Pick<
  SecurityInterestsApiClient,
  "getLedger" | "createInterest" | "addItem" | "transitionItem" | "confirm"
>;

function splitRefs(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

// Stage-9 security-perfection ledger. ONE interest per asset, each with its own
// per-requirement ledger. Requirements advance only along the closed status
// graph (the transition control offers only allowed targets, none preselected).
// Confirmation is a rationale-bearing independent-checker gate write, fail-closed
// when any requirement is not terminally satisfied. No polling; refresh is manual.
export function SecurityWorkspace({
  caseId,
  api = securityInterestsApi,
}: {
  caseId: string;
  api?: SecurityApi;
}) {
  const [ledger, setLedger] = useState<SecurityLedger | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setUnauthorized(false);
    setRefreshError(null);
    try {
      setLedger(await api.getLedger(caseId));
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 403) {
        setUnauthorized(true);
      } else {
        setError(getSecurityError(requestError));
      }
    } finally {
      setLoading(false);
    }
  }, [api, caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(async () => {
    setRefreshError(null);
    try {
      setLedger(await api.getLedger(caseId));
    } catch (requestError) {
      setRefreshError(getSecurityError(requestError));
    }
  }, [api, caseId]);

  if (loading) {
    return (
      <div aria-busy="true" aria-label="Đang tải biện pháp bảo đảm" className="case-skeleton" role="status">
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (unauthorized) {
    return (
      <>
        <CaseNav caseId={caseId} current="bao-dam" />
        <div className="state-panel" role="alert">
          <p>Bạn không có thẩm quyền xem sổ hoàn thiện biện pháp bảo đảm.</p>
        </div>
      </>
    );
  }

  if (error || !ledger) {
    return (
      <>
        <CaseNav caseId={caseId} current="bao-dam" />
        <div className="state-panel" role="alert">
          <p>{error ?? "Không thể đọc sổ hoàn thiện bảo đảm."}</p>
          <button className="button button-secondary" onClick={() => void load()} type="button">
            Thử tải lại
          </button>
        </div>
      </>
    );
  }

  const blockers = derivePerfectionBlockers(ledger.interests);

  return (
    <>
      <CaseNav caseId={caseId} current="bao-dam" />
      <div className="page-heading">
        <p className="eyebrow">Giai đoạn 9 · Hoàn thiện bảo đảm</p>
        <h1>Hoàn thiện biện pháp bảo đảm</h1>
      </div>

      <div className={styles.workspace}>
        {ledger.interests.length === 0 ? (
          <div className="empty-state">
            <p className="empty-state-title">Chưa có biện pháp bảo đảm nào</p>
            <p className="empty-state-hint">
              Mỗi tài sản bảo đảm là một biện pháp riêng, kèm các yêu cầu hoàn thiện được theo dõi
              độc lập. Thêm biện pháp đầu tiên bên dưới.
            </p>
          </div>
        ) : (
          <ul className={styles.list}>
            {ledger.interests.map((entry) => (
              <InterestCard
                caseId={caseId}
                api={api}
                entry={entry}
                key={entry.interest.id}
                onChanged={() => void refresh()}
              />
            ))}
          </ul>
        )}

        <section aria-labelledby="add-interest-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="add-interest-heading">
            Thêm biện pháp bảo đảm
          </h2>
          <CreateInterestForm
            onReload={() => void refresh()}
            onSubmit={async (input) => {
              await api.createInterest(caseId, input);
              await refresh();
            }}
          />
        </section>

        <section aria-labelledby="confirm-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="confirm-heading">
            Xác nhận hoàn thiện bảo đảm
          </h2>
          <p className={styles.sectionLead}>
            {blockers.confirmable
              ? "Mọi yêu cầu hoàn thiện đã ở trạng thái thỏa mãn. Người kiểm soát độc lập ghi xác nhận cổng HG_SECURITY_PERFECTION_CONFIRMED."
              : "Chưa thể xác nhận: còn biện pháp chưa có yêu cầu hoặc còn yêu cầu chưa hoàn tất. Máy chủ vẫn kiểm tra lại khi ghi."}
          </p>
          <RecordActionForm
            formatError={getSecurityError}
            hint="Chỉ người kiểm soát độc lập (khác người xác minh) mới xác nhận; máy chủ kiểm tra fail-closed."
            onReload={() => void refresh()}
            onSubmit={async (rationale) => {
              await api.confirm(caseId, rationale);
              await refresh();
            }}
            rationaleLabel="Lý do xác nhận hoàn thiện bảo đảm"
            submitLabel="Xác nhận hoàn thiện bảo đảm"
          />
        </section>

        {refreshError ? (
          <div className="state-panel" role="alert">
            <p>Đã ghi vào sổ, nhưng không tải lại được bản mới nhất: {refreshError}</p>
            <button className="button button-secondary" onClick={() => void refresh()} type="button">
              Tải lại
            </button>
          </div>
        ) : null}
      </div>
    </>
  );
}

function InterestCard({
  caseId,
  api,
  entry,
  onChanged,
}: {
  caseId: string;
  api: SecurityApi;
  entry: InterestWithItems;
  onChanged: () => void;
}) {
  const { interest, items } = entry;
  return (
    <li className={styles.entry}>
      <div className={styles.entryHead}>
        <p className={styles.entryTitle}>{interest.assetDescription}</p>
        <span className="status-chip status-chip--info">
          {labelOrUnsupported(ASSET_KIND_LABELS, String(interest.assetKind))}
        </span>
      </div>
      <p className={styles.entryMeta}>
        Chủ sở hữu: {interest.ownerName ?? "—"} · Tham chiếu định giá:{" "}
        {interest.valuationReference ?? "—"} · Mã {shortId(interest.id)}
      </p>
      {interest.notes ? <p className={styles.entryText}>{interest.notes}</p> : null}

      {items.length === 0 ? (
        <p className={styles.entryMeta}>
          Chưa có yêu cầu hoàn thiện nào cho biện pháp này (là một điều kiện chặn xác nhận).
        </p>
      ) : (
        <ul className={styles.items}>
          {items.map((item) => (
            <ItemRow
              api={api}
              caseId={caseId}
              item={item}
              key={item.id}
              onChanged={onChanged}
            />
          ))}
        </ul>
      )}

      <AddItemForm
        onReload={onChanged}
        onSubmit={async (input) => {
          await api.addItem(caseId, interest.id, input);
          onChanged();
        }}
      />
    </li>
  );
}

function ItemRow({
  caseId,
  api,
  item,
  onChanged,
}: {
  caseId: string;
  api: SecurityApi;
  item: PerfectionItem;
  onChanged: () => void;
}) {
  const targets = allowedItemTransitions(item.status);
  return (
    <li className={styles.item}>
      <div className={styles.entryHead}>
        <p className={styles.entryText}>{item.requirement}</p>
        <span className="status-chip status-chip--muted">
          {labelOrUnsupported(PERFECTION_STATUS_LABELS, String(item.status))}
        </span>
      </div>
      <p className={styles.entryMeta}>
        Tham chiếu hồ sơ: {item.filingReference ?? "—"} · Hiệu lực {formatDate(item.effectiveDate)}{" "}
        · Hết hạn {formatDate(item.expiryDate)}
      </p>
      {item.evidenceRefs.length > 0 ? (
        <ul className={styles.refList}>
          {item.evidenceRefs.map((ref) => (
            <li className={styles.ref} key={ref}>
              {ref}
            </li>
          ))}
        </ul>
      ) : null}
      {item.completedAt ? (
        <p className={styles.entryMeta}>
          Hoàn thiện bởi {shortId(item.completedBy)} · {formatDateTime(item.completedAt)}
        </p>
      ) : null}
      {targets.length > 0 ? (
        <ItemTransitionForm
          onReload={onChanged}
          onSubmit={async (input) => {
            await api.transitionItem(caseId, item.id, input);
            onChanged();
          }}
          targets={targets}
        />
      ) : (
        <p className={styles.entryMeta}>Trạng thái kết thúc: không còn bước chuyển tiếp.</p>
      )}
    </li>
  );
}

function CreateInterestForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: CreateInterestInput) => Promise<void>;
  onReload: () => void;
}) {
  const [assetKind, setAssetKind] = useState<string>("");
  const [assetDescription, setAssetDescription] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [valuationReference, setValuationReference] = useState("");
  const [notes, setNotes] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const description = assetDescription.trim();
    if (!assetKind) {
      setFieldError("Chọn loại tài sản bảo đảm.");
      return;
    }
    if (description.length === 0) {
      setFieldError("Nhập mô tả tài sản bảo đảm.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        assetKind: assetKind as CreateInterestInput["assetKind"],
        assetDescription: description,
        ownerName: ownerName.trim() || undefined,
        valuationReference: valuationReference.trim() || undefined,
        notes: notes.trim() || undefined,
      });
      setAssetKind("");
      setAssetDescription("");
      setOwnerName("");
      setValuationReference("");
      setNotes("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getSecurityError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <div className={styles.field}>
        <span className={styles.fieldLabel}>
          Loại tài sản <span className={styles.required}>*</span>
        </span>
        <div className={styles.radioGroup} role="radiogroup" aria-label="Loại tài sản bảo đảm">
          {SECURITY_ASSET_KINDS.map((kind) => (
            <label
              className={styles.radioOption}
              data-checked={assetKind === kind ? "true" : "false"}
              key={kind}
            >
              <input
                checked={assetKind === kind}
                disabled={pending}
                name="asset-kind"
                onChange={() => {
                  setAssetKind(kind);
                  if (fieldError) setFieldError(null);
                }}
                type="radio"
                value={kind}
              />
              <span>{ASSET_KIND_LABELS[kind]}</span>
            </label>
          ))}
        </div>
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="interest-description">
          Mô tả tài sản <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id="interest-description"
          maxLength={2000}
          onChange={(event) => {
            setAssetDescription(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={assetDescription}
        />
      </div>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="interest-owner">
            Chủ sở hữu (không bắt buộc)
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id="interest-owner"
            maxLength={500}
            onChange={(event) => setOwnerName(event.target.value)}
            value={ownerName}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="interest-valuation">
            Tham chiếu định giá (không bắt buộc)
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id="interest-valuation"
            maxLength={500}
            onChange={(event) => setValuationReference(event.target.value)}
            value={valuationReference}
          />
        </div>
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="interest-notes">
          Ghi chú (không bắt buộc)
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id="interest-notes"
          maxLength={4000}
          onChange={(event) => setNotes(event.target.value)}
          value={notes}
        />
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
            <button className="button button-secondary" onClick={() => onReload()} type="button">
              Tải lại
            </button>
          ) : null}
        </div>
      ) : null}
      <div className={styles.formActions}>
        <button aria-busy={pending} className={styles.submit} disabled={pending} type="submit">
          {pending ? "Đang thêm biện pháp…" : "Thêm biện pháp bảo đảm"}
        </button>
      </div>
    </form>
  );
}

function AddItemForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: AddItemInput) => Promise<void>;
  onReload: () => void;
}) {
  const formId = useId();
  const [requirement, setRequirement] = useState("");
  const [evidence, setEvidence] = useState("");
  const [filingReference, setFilingReference] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const text = requirement.trim();
    if (text.length === 0) {
      setFieldError("Nhập nội dung yêu cầu hoàn thiện.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        requirement: text,
        evidenceRefs: splitRefs(evidence),
        filingReference: filingReference.trim() || undefined,
        effectiveDate: effectiveDate || undefined,
        expiryDate: expiryDate || undefined,
      });
      setRequirement("");
      setEvidence("");
      setFilingReference("");
      setEffectiveDate("");
      setExpiryDate("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getSecurityError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <p className={styles.formHint}>Thêm một yêu cầu hoàn thiện cho biện pháp này (bắt đầu ở trạng thái chờ xử lý).</p>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-requirement`}>
          Yêu cầu hoàn thiện <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id={`${formId}-requirement`}
          onChange={(event) => {
            setRequirement(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={requirement}
        />
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel}>Tham chiếu bằng chứng (mỗi dòng một mục, không bắt buộc)</label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          onChange={(event) => setEvidence(event.target.value)}
          value={evidence}
        />
      </div>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel}>Tham chiếu hồ sơ (không bắt buộc)</label>
          <input
            className={styles.input}
            disabled={pending}
            maxLength={500}
            onChange={(event) => setFilingReference(event.target.value)}
            value={filingReference}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel}>Ngày hiệu lực</label>
          <input
            className={styles.input}
            disabled={pending}
            onChange={(event) => setEffectiveDate(event.target.value)}
            type="date"
            value={effectiveDate}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel}>Ngày hết hạn</label>
          <input
            className={styles.input}
            disabled={pending}
            onChange={(event) => setExpiryDate(event.target.value)}
            type="date"
            value={expiryDate}
          />
        </div>
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
            <button className="button button-secondary" onClick={() => onReload()} type="button">
              Tải lại
            </button>
          ) : null}
        </div>
      ) : null}
      <div className={styles.formActions}>
        <button aria-busy={pending} className={styles.submit} disabled={pending} type="submit">
          {pending ? "Đang thêm yêu cầu…" : "Thêm yêu cầu hoàn thiện bảo đảm"}
        </button>
      </div>
    </form>
  );
}

function ItemTransitionForm({
  targets,
  onSubmit,
  onReload,
}: {
  targets: readonly PerfectionStatus[];
  onSubmit: (input: TransitionItemInput) => Promise<void>;
  onReload: () => void;
}) {
  const groupName = useId();
  const [target, setTarget] = useState<PerfectionStatus | "">("");
  const [rationale, setRationale] = useState("");
  const [evidence, setEvidence] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    if (!target) {
      setFieldError("Chọn trạng thái đích cho yêu cầu.");
      return;
    }
    const refs = splitRefs(evidence);
    if (target === "COMPLETED" && refs.length === 0) {
      setFieldError("Trạng thái hoàn thiện phải kèm ít nhất một tham chiếu bằng chứng.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        toStatus: target,
        rationale: rationale.trim() || undefined,
        evidenceRefs: refs,
      });
      setTarget("");
      setRationale("");
      setEvidence("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getSecurityError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <div className={styles.field}>
        <span className={styles.fieldLabel}>
          Chuyển trạng thái <span className={styles.required}>*</span>
        </span>
        <div className={styles.radioGroup} role="radiogroup" aria-label="Trạng thái đích">
          {targets.map((option) => (
            <label
              className={styles.radioOption}
              data-checked={target === option ? "true" : "false"}
              key={option}
            >
              <input
                checked={target === option}
                disabled={pending}
                name={groupName}
                onChange={() => {
                  setTarget(option);
                  if (fieldError) setFieldError(null);
                }}
                type="radio"
                value={option}
              />
              <span>{ITEM_TRANSITION_LABELS[option]}</span>
            </label>
          ))}
        </div>
      </div>
      {target === "COMPLETED" ? (
        <div className={styles.field}>
          <label className={styles.fieldLabel}>
            Tham chiếu bằng chứng (mỗi dòng một mục) <span className={styles.required}>*</span>
          </label>
          <textarea
            className={styles.textarea}
            disabled={pending}
            onChange={(event) => {
              setEvidence(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            value={evidence}
          />
        </div>
      ) : null}
      <div className={styles.field}>
        <label className={styles.fieldLabel}>Lý do (không bắt buộc)</label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          maxLength={4000}
          onChange={(event) => setRationale(event.target.value)}
          value={rationale}
        />
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
            <button className="button button-secondary" onClick={() => onReload()} type="button">
              Tải lại
            </button>
          ) : null}
        </div>
      ) : null}
      <div className={styles.formActions}>
        <button aria-busy={pending} className={styles.submit} disabled={pending} type="submit">
          {pending ? "Đang chuyển trạng thái…" : "Ghi nhận chuyển trạng thái yêu cầu"}
        </button>
      </div>
    </form>
  );
}
