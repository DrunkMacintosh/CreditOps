"use client";

import React, { useCallback, useEffect, useId, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  allowedConditionTransitions,
  CONDITION_STATUS_LABELS,
  CONDITION_TRANSITION_LABELS,
  ConditionsApiClient,
  conditionsApi,
  formatDate,
  formatDateTime,
  getConditionError,
  isRationaleRequired,
  labelOrUnsupported,
  shortId,
  type ConditionLedger,
  type ConditionStatus,
  type CreateConditionInput,
  type DisbursementCondition,
  type TransitionConditionInput,
} from "../../lib/api/conditions";
import { RecordActionForm } from "../gates/record-action-form";
import { CaseNav } from "../shell/case-nav";
import styles from "../gates/gates.module.css";

type ConditionsApi = Pick<
  ConditionsApiClient,
  "getLedger" | "createCondition" | "transition" | "confirm"
>;

function splitRefs(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

// Stage-10 disbursement-condition ledger. A human-only ledger of the 8 statuses;
// transitions move along the closed graph under the correct authority (waiver /
// not-applicable rulings require a rationale). Confirmation is a fail-closed
// independent-checker gate write over a NON-empty, all-satisfied ledger. No
// polling; refresh is manual.
export function ConditionWorkspace({
  caseId,
  api = conditionsApi,
}: {
  caseId: string;
  api?: ConditionsApi;
}) {
  const [ledger, setLedger] = useState<ConditionLedger | null>(null);
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
        setError(getConditionError(requestError));
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
      setRefreshError(getConditionError(requestError));
    }
  }, [api, caseId]);

  if (loading) {
    return (
      <div aria-busy="true" aria-label="Đang tải điều kiện giải ngân" className="case-skeleton" role="status">
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (unauthorized) {
    return (
      <>
        <CaseNav caseId={caseId} current="dieu-kien-giai-ngan" />
        <div className="state-panel" role="alert">
          <p>Bạn không có vai trò tham gia hồ sơ để xem điều kiện giải ngân.</p>
        </div>
      </>
    );
  }

  if (error || !ledger) {
    return (
      <>
        <CaseNav caseId={caseId} current="dieu-kien-giai-ngan" />
        <div className="state-panel" role="alert">
          <p>{error ?? "Không thể đọc sổ điều kiện giải ngân."}</p>
          <button className="button button-secondary" onClick={() => void load()} type="button">
            Thử tải lại
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <CaseNav caseId={caseId} current="dieu-kien-giai-ngan" />
      <div className="page-heading">
        <p className="eyebrow">Giai đoạn 10 · Điều kiện giải ngân</p>
        <h1>Điều kiện giải ngân</h1>
      </div>

      <div className={styles.workspace}>
        <div className={styles.gateRow}>
          <span
            className={`status-chip ${
              ledger.confirmable ? "status-chip--ok" : "status-chip--amber"
            }`}
          >
            {ledger.confirmable
              ? "Sẵn sàng xác nhận"
              : "Chưa đủ điều kiện để xác nhận"}
          </span>
          <span className={styles.meta}>Hồ sơ v{ledger.caseVersion}</span>
        </div>

        {ledger.conditions.length === 0 ? (
          <div className="empty-state">
            <p className="empty-state-title">Chưa có điều kiện giải ngân nào</p>
            <p className="empty-state-hint">
              Điều kiện chỉ mở được sau khi có quyết định phê duyệt tín dụng. Sổ rỗng không bao
              giờ được coi là đã xác nhận — cần ít nhất một điều kiện được xử lý dứt điểm.
            </p>
          </div>
        ) : (
          <ul className={styles.list}>
            {ledger.conditions.map((condition) => (
              <ConditionCard
                api={api}
                caseId={caseId}
                condition={condition}
                key={condition.id}
                onChanged={() => void refresh()}
              />
            ))}
          </ul>
        )}

        <section aria-labelledby="create-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="create-heading">
            Mở điều kiện giải ngân
          </h2>
          <CreateConditionForm
            onReload={() => void refresh()}
            onSubmit={async (input) => {
              await api.createCondition(caseId, input);
              await refresh();
            }}
          />
        </section>

        <section aria-labelledby="confirm-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="confirm-heading">
            Xác nhận điều kiện giải ngân
          </h2>
          <p className={styles.sectionLead}>
            {ledger.confirmable
              ? "Mọi điều kiện đã được xác minh / miễn trừ / xác định không áp dụng. Người kiểm soát độc lập (khác người đã xác minh) ghi xác nhận cổng HG_DISBURSEMENT_CONDITIONS_CONFIRMED."
              : "Chưa thể xác nhận: còn điều kiện chưa xử lý dứt điểm, hoặc sổ đang rỗng. Máy chủ vẫn kiểm tra fail-closed khi ghi."}
          </p>
          <RecordActionForm
            formatError={getConditionError}
            hint="Xác nhận không nhập lý do; máy chủ kiểm tra fail-closed và tách biệt người xác minh với người xác nhận."
            onReload={() => void refresh()}
            onSubmit={async () => {
              await api.confirm(caseId);
              await refresh();
            }}
            showRationale={false}
            submitLabel="Xác nhận điều kiện giải ngân"
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

function ConditionCard({
  caseId,
  api,
  condition,
  onChanged,
}: {
  caseId: string;
  api: ConditionsApi;
  condition: DisbursementCondition;
  onChanged: () => void;
}) {
  const targets = allowedConditionTransitions(condition.status);
  return (
    <li className={styles.entry}>
      <div className={styles.entryHead}>
        <p className={styles.entryText}>{condition.conditionText}</p>
        <span className="status-chip status-chip--muted">
          {labelOrUnsupported(CONDITION_STATUS_LABELS, String(condition.status))}
        </span>
      </div>
      <p className={styles.entryMeta}>
        Phụ trách: {condition.owner ?? "—"} · Hạn: {formatDate(condition.dueDate)} · Quyết định
        nguồn {shortId(condition.decisionId)} · {formatDateTime(condition.createdAt)}
      </p>
      {condition.evidenceRefs.length > 0 ? (
        <ul className={styles.refList}>
          {condition.evidenceRefs.map((ref) => (
            <li className={styles.ref} key={ref}>
              {ref}
            </li>
          ))}
        </ul>
      ) : null}
      {targets.length > 0 ? (
        <ConditionTransitionForm
          onReload={onChanged}
          onSubmit={async (input) => {
            await api.transition(caseId, condition.id, input);
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

function CreateConditionForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: CreateConditionInput) => Promise<void>;
  onReload: () => void;
}) {
  const formId = useId();
  const [conditionText, setConditionText] = useState("");
  const [owner, setOwner] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const text = conditionText.trim();
    if (text.length === 0) {
      setFieldError("Nhập nội dung điều kiện giải ngân.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        conditionText: text,
        owner: owner.trim() || undefined,
        dueDate: dueDate || undefined,
      });
      setConditionText("");
      setOwner("");
      setDueDate("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getConditionError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-text`}>
          Nội dung điều kiện <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id={`${formId}-text`}
          maxLength={4000}
          onChange={(event) => {
            setConditionText(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={conditionText}
        />
      </div>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-owner`}>
            Phụ trách (không bắt buộc)
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-owner`}
            maxLength={400}
            onChange={(event) => setOwner(event.target.value)}
            value={owner}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-due`}>
            Hạn xử lý (không bắt buộc)
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-due`}
            onChange={(event) => setDueDate(event.target.value)}
            type="date"
            value={dueDate}
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
          {pending ? "Đang mở điều kiện…" : "Mở điều kiện giải ngân"}
        </button>
      </div>
    </form>
  );
}

function ConditionTransitionForm({
  targets,
  onSubmit,
  onReload,
}: {
  targets: readonly ConditionStatus[];
  onSubmit: (input: TransitionConditionInput) => Promise<void>;
  onReload: () => void;
}) {
  const groupName = useId();
  const [target, setTarget] = useState<ConditionStatus | "">("");
  const [rationale, setRationale] = useState("");
  const [evidence, setEvidence] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const rationaleRequired = target !== "" && isRationaleRequired(target);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    if (!target) {
      setFieldError("Chọn trạng thái đích cho điều kiện.");
      return;
    }
    const note = rationale.trim();
    if (rationaleRequired && note.length === 0) {
      setFieldError("Miễn trừ / không áp dụng là quyết định có thẩm quyền: bắt buộc nhập lý do.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        toStatus: target,
        rationale: note || undefined,
        evidenceRefs: splitRefs(evidence),
      });
      setTarget("");
      setRationale("");
      setEvidence("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getConditionError(requestError));
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
              <span>{CONDITION_TRANSITION_LABELS[option]}</span>
            </label>
          ))}
        </div>
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${groupName}-rationale`}>
          Lý do{" "}
          {rationaleRequired ? (
            <span className={styles.required}>* (ghi thẩm quyền)</span>
          ) : (
            "(không bắt buộc)"
          )}
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id={`${groupName}-rationale`}
          maxLength={4000}
          onChange={(event) => {
            setRationale(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={rationale}
        />
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel}>
          Tham chiếu bằng chứng (mỗi dòng một mục, không bắt buộc)
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          onChange={(event) => setEvidence(event.target.value)}
          value={evidence}
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
          {pending ? "Đang chuyển trạng thái…" : "Ghi nhận chuyển trạng thái điều kiện"}
        </button>
      </div>
    </form>
  );
}
