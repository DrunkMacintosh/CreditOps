"use client";

import React, { useCallback, useEffect, useId, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  OpenRecoveryInput,
  RECEIPT_KIND_LABELS,
  RECOVERY_STATUS_LABELS,
  RecoveryCase,
  RecoveryCases,
  RecoveryOptionInput,
  SettlementCheck,
  SettlementCheckInput,
  SettlementRecoveryApiClient,
  SettlementView,
  formatDateTime,
  getRecoveryNotTriggeredDetails,
  getSettlementError,
  getSettlementIneligibleDetails,
  isSameActorForbidden,
  labelOrUnsupported,
  settlementRecoveryApi,
  shortId,
  type RecoveryNotTriggeredDetails,
  type SettlementIneligibleDetails,
} from "../../lib/api/settlement-recovery";
import { RecordActionForm } from "../gates/record-action-form";
import { CaseNav } from "../shell/case-nav";
import gates from "../gates/gates.module.css";
import styles from "./settlement.module.css";

type SettlementApi = Pick<
  SettlementRecoveryApiClient,
  | "getSettlement"
  | "createSettlementCheck"
  | "confirmSettlement"
  | "getRecovery"
  | "openRecovery"
  | "approveStrategy"
>;

// Stage-14 settlement (14A) + recovery-preparation (14B) workspace. Two mutually
// exclusive, fail-closed branches: settlement records a ledger check (server
// DERIVES eligibility), confirms with labelled MOCK receipts and a human
// rationale; recovery opens ONLY from a deterministic sustained-shortfall trigger
// PLUS a mandatory escalation rationale, and a DIFFERENT authority approves the
// strategy. Money figures are the server's exact strings, rendered verbatim.
export function SettlementWorkspace({
  caseId,
  api = settlementRecoveryApi,
}: {
  caseId: string;
  api?: SettlementApi;
}) {
  const [view, setView] = useState<SettlementView | null>(null);
  const [recovery, setRecovery] = useState<RecoveryCases | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const fetchBoth = useCallback(async () => {
    const [nextView, nextRecovery] = await Promise.all([
      api.getSettlement(caseId),
      api.getRecovery(caseId),
    ]);
    return { nextView, nextRecovery };
  }, [api, caseId]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setUnauthorized(false);
    setRefreshError(null);
    try {
      const { nextView, nextRecovery } = await fetchBoth();
      setView(nextView);
      setRecovery(nextRecovery);
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 403) {
        setUnauthorized(true);
      } else {
        setError(getSettlementError(requestError));
      }
    } finally {
      setLoading(false);
    }
  }, [fetchBoth]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(async () => {
    setRefreshError(null);
    try {
      const { nextView, nextRecovery } = await fetchBoth();
      setView(nextView);
      setRecovery(nextRecovery);
    } catch (requestError) {
      setRefreshError(getSettlementError(requestError));
    }
  }, [fetchBoth]);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Đang tải tất toán và xử lý nợ"
        className="case-skeleton"
        role="status"
      >
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (unauthorized) {
    return (
      <>
        <CaseNav caseId={caseId} current="tat-toan-xu-ly-no" />
        <div className="state-panel" role="alert">
          <p>Bạn không có vai trò tham gia hồ sơ để xem tất toán và xử lý nợ.</p>
        </div>
      </>
    );
  }

  if (error || !view || !recovery) {
    return (
      <>
        <CaseNav caseId={caseId} current="tat-toan-xu-ly-no" />
        <div className="state-panel" role="alert">
          <p>{error ?? "Không thể đọc tất toán và xử lý nợ."}</p>
          <button className="button button-secondary" onClick={() => void load()} type="button">
            Thử tải lại
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <CaseNav caseId={caseId} current="tat-toan-xu-ly-no" />
      <div className="page-heading">
        <p className="eyebrow">Giai đoạn 14 · Tất toán và xử lý nợ</p>
        <h1>Tất toán và xử lý nợ</h1>
      </div>

      <div className={gates.workspace}>
        <h2 className={styles.branchTitle}>14A · Tất toán khoản vay</h2>

        <div className={styles.chipRow}>
          <span
            className={`status-chip ${
              view.confirmable ? "status-chip--ok" : "status-chip--amber"
            }`}
          >
            {view.confirmable ? "Đủ điều kiện tất toán" : "Chưa đủ điều kiện tất toán"}
          </span>
          <span className={styles.meta}>Hồ sơ v{view.caseVersion}</span>
        </div>

        <section aria-labelledby="check-heading" className={gates.section}>
          <h3 className={gates.sectionTitle} id="check-heading">
            Kiểm tra điều kiện tất toán
          </h3>
          <p className={gates.sectionLead}>
            Nhập số liệu ledger để máy chủ xác định điều kiện (dư nợ bằng 0 và không còn
            exception). Số liệu không đủ điều kiện bị từ chối với chi tiết suy ra.
          </p>
          <SettlementCheckForm
            onSubmit={async (input) => {
              await api.createSettlementCheck(caseId, input);
              await refresh();
            }}
          />
          <SettlementChecksList checks={view.checks} />
        </section>

        <section aria-labelledby="confirm-heading" className={gates.section}>
          <h3 className={gates.sectionTitle} id="confirm-heading">
            Xác nhận tất toán
          </h3>
          <p className={gates.sectionLead}>
            {view.confirmable
              ? "Đã có settlement check đủ điều kiện. Ghi lý do xác nhận; máy chủ ghi các biên nhận mô phỏng và thỏa cổng HG_SETTLEMENT_CONFIRMED."
              : "Chưa thể xác nhận: cần một settlement check đủ điều kiện (dư nợ bằng 0). Máy chủ vẫn kiểm tra fail-closed khi ghi."}
          </p>
          <RecordActionForm
            formatError={getSettlementError}
            hint="Xác nhận ghi các biên nhận mô phỏng (không thực hiện tất toán/giải chấp thật)."
            onReload={() => void refresh()}
            onSubmit={async () => {
              await api.confirmSettlement(caseId);
              await refresh();
            }}
            rationaleLabel="Lý do xác nhận tất toán"
            submitLabel="Xác nhận tất toán"
          />
          <SettlementReceipts view={view} />
        </section>

        <h2 className={styles.branchTitle}>14B · Chuẩn bị xử lý nợ</h2>
        <div className={styles.chipRow}>
          <span className={styles.meta}>Hồ sơ v{recovery.caseVersion}</span>
        </div>

        <section aria-labelledby="open-recovery-heading" className={gates.section}>
          <h3 className={gates.sectionTitle} id="open-recovery-heading">
            Mở hồ sơ xử lý nợ
          </h3>
          <p className={gates.sectionLead}>
            Chỉ mở được khi trigger xác định (shortfall kéo dài đủ số kỳ) VÀ có lý do escalate
            của con người. Không bao giờ mở từ điểm số mô hình.
          </p>
          <OpenRecoveryForm
            onSubmit={async (input) => {
              await api.openRecovery(caseId, input);
              await refresh();
            }}
          />
        </section>

        <section aria-labelledby="recovery-list-heading" className={gates.section}>
          <h3 className={gates.sectionTitle} id="recovery-list-heading">
            Hồ sơ xử lý nợ
          </h3>
          {recovery.recoveryCases.length === 0 ? (
            <p className={gates.sectionLead}>Chưa có hồ sơ xử lý nợ nào.</p>
          ) : (
            <ul className={gates.list}>
              {recovery.recoveryCases.map((recoveryCase) => (
                <RecoveryCaseCard
                  key={recoveryCase.id}
                  onApprove={async () => {
                    await api.approveStrategy(caseId, recoveryCase.id);
                    await refresh();
                  }}
                  recoveryCase={recoveryCase}
                />
              ))}
            </ul>
          )}
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

function SettlementChecksList({ checks }: { checks: SettlementCheck[] }) {
  if (checks.length === 0) {
    return <p className={gates.sectionLead}>Chưa có lần kiểm tra tất toán nào.</p>;
  }
  return (
    <ul className={gates.list}>
      {checks.map((check) => (
        <li className={gates.entry} key={check.id}>
          <div className={gates.entryHead}>
            <span
              className={`status-chip ${
                check.zeroBalanceConfirmed ? "status-chip--ok" : "status-chip--amber"
              }`}
            >
              {check.zeroBalanceConfirmed ? "Dư nợ bằng 0" : "Còn dư nợ"}
            </span>
            <span className={styles.meta}>{formatDateTime(check.createdAt)}</span>
          </div>
          <dl className={styles.detailGrid}>
            <DetailItem label="Dư nợ gốc" value={check.outstandingPrincipal} />
            <DetailItem label="Dư lãi" value={check.outstandingInterest} />
            <DetailItem label="Dư phí" value={check.outstandingFees} />
            <DetailItem label="Số exception còn mở" value={String(check.openExceptionCount)} />
          </dl>
        </li>
      ))}
    </ul>
  );
}

function SettlementReceipts({ view }: { view: SettlementView }) {
  if (view.receipts.length === 0) return null;
  return (
    <div>
      <h4 className={styles.receiptKind}>Biên nhận mô phỏng</h4>
      <ul className={styles.receiptList}>
        {view.receipts.map((receipt) => (
          <li className={styles.receipt} key={receipt.id}>
            <div className={styles.receiptHead}>
              <p className={styles.receiptKind}>
                {labelOrUnsupported(RECEIPT_KIND_LABELS, String(receipt.kind))}
              </p>
              <span className={styles.mockBadge}>Biên nhận mô phỏng</span>
            </div>
            {receipt.note ? <p className={styles.receiptNote}>{receipt.note}</p> : null}
            <p className={styles.receiptNote}>{formatDateTime(receipt.createdAt)}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RecoveryCaseCard({
  recoveryCase,
  onApprove,
}: {
  recoveryCase: RecoveryCase;
  onApprove: () => Promise<void>;
}) {
  const isPreparing = recoveryCase.status === "PREPARING";
  return (
    <li className={gates.entry}>
      <div className={gates.entryHead}>
        <span className="status-chip status-chip--muted">
          {labelOrUnsupported(RECOVERY_STATUS_LABELS, String(recoveryCase.status))}
        </span>
        <span className={styles.meta}>
          Escalate bởi {shortId(recoveryCase.escalatedBy)}
          {recoveryCase.approvedBy ? ` · Duyệt bởi ${shortId(recoveryCase.approvedBy)}` : ""}
        </span>
      </div>
      <p className={gates.entryText}>{recoveryCase.triggerSummary}</p>
      <p className={gates.entryMeta}>Lý do escalate: {recoveryCase.escalationRationale}</p>
      {recoveryCase.evidenceRefs.length > 0 ? (
        <ul className={gates.refList}>
          {recoveryCase.evidenceRefs.map((ref) => (
            <li className={gates.ref} key={ref}>
              {ref}
            </li>
          ))}
        </ul>
      ) : null}
      {recoveryCase.options.length > 0 ? (
        <ul className={styles.optionList}>
          {recoveryCase.options.map((option, index) => (
            <li className={styles.option} key={`${recoveryCase.id}-${index}`}>
              <p className={styles.optionLabel}>{option.label}</p>
              <p className={styles.optionText}>{option.description}</p>
              <p className={styles.optionMeta}>Hệ quả: {option.consequences}</p>
              {option.dependencies ? (
                <p className={styles.optionMeta}>Phụ thuộc: {option.dependencies}</p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {isPreparing ? (
        <RecordActionForm
          formatError={formatApprovalError}
          hint="Người phê duyệt phải KHÁC người đã escalate hồ sơ. Duyệt thỏa cổng HG_RECOVERY_STRATEGY_APPROVED."
          onSubmit={onApprove}
          rationaleLabel="Ghi chú phê duyệt (không bắt buộc)"
          rationaleRequired={false}
          showRationale={false}
          submitLabel="Duyệt phương án xử lý nợ"
        />
      ) : (
        <p className={gates.entryMeta}>Phương án đã được phê duyệt.</p>
      )}
    </li>
  );
}

// A same-actor rejection is its own honest branch, distinct from a generic 409.
function formatApprovalError(error: unknown): string {
  if (isSameActorForbidden(error)) {
    return "Không thể duyệt: người phê duyệt chiến lược phải khác với người đã escalate hồ sơ.";
  }
  return getSettlementError(error);
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.detailItem}>
      <dt className={styles.detailLabel}>{label}</dt>
      <dd className={styles.detailValue}>{value}</dd>
    </div>
  );
}

function SettlementCheckForm({
  onSubmit,
}: {
  onSubmit: (input: SettlementCheckInput) => Promise<void>;
}) {
  const formId = useId();
  const [principal, setPrincipal] = useState("");
  const [interest, setInterest] = useState("");
  const [fees, setFees] = useState("");
  const [exceptions, setExceptions] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [ineligible, setIneligible] = useState<SettlementIneligibleDetails | null>(null);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setIneligible(null);
    const openExceptionCount = Number.parseInt(exceptions, 10);
    if (
      principal.trim().length === 0 ||
      interest.trim().length === 0 ||
      fees.trim().length === 0 ||
      Number.isNaN(openExceptionCount) ||
      openExceptionCount < 0
    ) {
      setFieldError("Nhập đầy đủ dư nợ gốc, lãi, phí và số exception còn mở (>= 0).");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        outstandingPrincipal: principal.trim(),
        outstandingInterest: interest.trim(),
        outstandingFees: fees.trim(),
        openExceptionCount,
      });
      setPrincipal("");
      setInterest("");
      setFees("");
      setExceptions("");
    } catch (requestError) {
      const details = getSettlementIneligibleDetails(requestError);
      if (details) {
        setIneligible(details);
      } else {
        setSubmitError(getSettlementError(requestError));
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={gates.form} noValidate onSubmit={handleSubmit}>
      <div className={gates.fieldRow}>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-principal`}>
            Dư nợ gốc <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-principal`}
            inputMode="decimal"
            onChange={(event) => setPrincipal(event.target.value)}
            value={principal}
          />
        </div>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-interest`}>
            Dư lãi <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-interest`}
            inputMode="decimal"
            onChange={(event) => setInterest(event.target.value)}
            value={interest}
          />
        </div>
      </div>
      <div className={gates.fieldRow}>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-fees`}>
            Dư phí <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-fees`}
            inputMode="decimal"
            onChange={(event) => setFees(event.target.value)}
            value={fees}
          />
        </div>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-exceptions`}>
            Số exception còn mở <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-exceptions`}
            inputMode="numeric"
            onChange={(event) => setExceptions(event.target.value)}
            value={exceptions}
          />
        </div>
      </div>
      {fieldError ? (
        <p className={gates.fieldError} role="alert">
          {fieldError}
        </p>
      ) : null}
      {ineligible ? (
        <div className={styles.ineligibleBanner} role="alert">
          <p className={styles.ineligibleTitle}>Số liệu ledger chưa đủ điều kiện tất toán</p>
          <dl className={styles.detailGrid}>
            <DetailItem label="Dư nợ bằng 0" value={ineligible.zeroBalance ? "Có" : "Không"} />
            <DetailItem label="Dư nợ gốc" value={ineligible.outstandingPrincipal} />
            <DetailItem label="Dư lãi" value={ineligible.outstandingInterest} />
            <DetailItem label="Dư phí" value={ineligible.outstandingFees} />
            <DetailItem
              label="Số exception còn mở"
              value={String(ineligible.openExceptionCount)}
            />
          </dl>
        </div>
      ) : null}
      {submitError ? (
        <div className={gates.submitError} role="alert">
          <p>{submitError}</p>
        </div>
      ) : null}
      <div className={gates.formActions}>
        <button aria-busy={pending} className={gates.submit} disabled={pending} type="submit">
          {pending ? "Đang kiểm tra…" : "Ghi nhận kiểm tra tất toán"}
        </button>
      </div>
    </form>
  );
}

let optionKeySeq = 0;

interface OptionDraft extends RecoveryOptionInput {
  key: string;
}

function OpenRecoveryForm({
  onSubmit,
}: {
  onSubmit: (input: OpenRecoveryInput) => Promise<void>;
}) {
  const formId = useId();
  const [outstandingTotal, setOutstandingTotal] = useState("");
  const [periodsInShortfall, setPeriodsInShortfall] = useState("");
  const [triggerSummary, setTriggerSummary] = useState("");
  const [escalationRationale, setEscalationRationale] = useState("");
  const [evidence, setEvidence] = useState("");
  const [options, setOptions] = useState<OptionDraft[]>([
    { key: `opt-${optionKeySeq++}`, label: "", description: "", consequences: "", dependencies: "" },
  ]);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [notTriggered, setNotTriggered] = useState<RecoveryNotTriggeredDetails | null>(null);
  const [pending, setPending] = useState(false);

  const updateOption = (key: string, patch: Partial<RecoveryOptionInput>) => {
    setOptions((prior) => prior.map((option) => (option.key === key ? { ...option, ...patch } : option)));
  };

  const evidenceRefs = evidence
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setNotTriggered(null);
    const periods = Number.parseInt(periodsInShortfall, 10);
    if (
      outstandingTotal.trim().length === 0 ||
      Number.isNaN(periods) ||
      periods < 0 ||
      triggerSummary.trim().length === 0
    ) {
      setFieldError("Nhập tổng dư nợ, số kỳ shortfall (>= 0) và tóm tắt trigger.");
      return;
    }
    if (escalationRationale.trim().length === 0) {
      setFieldError("Lý do trình cấp có thẩm quyền là bắt buộc.");
      return;
    }
    if (evidenceRefs.length === 0) {
      setFieldError("Cần ít nhất một tham chiếu bằng chứng (mỗi dòng một mục).");
      return;
    }
    const cleanedOptions = options
      .map((option) => ({
        label: option.label.trim(),
        description: option.description.trim(),
        consequences: option.consequences.trim(),
        dependencies: option.dependencies?.trim() ? option.dependencies.trim() : undefined,
      }))
      .filter(
        (option) =>
          option.label.length > 0 ||
          option.description.length > 0 ||
          option.consequences.length > 0,
      );
    if (cleanedOptions.length === 0) {
      setFieldError("Cần ít nhất một phương án xử lý nợ.");
      return;
    }
    if (
      cleanedOptions.some(
        (option) =>
          option.label.length === 0 ||
          option.description.length === 0 ||
          option.consequences.length === 0,
      )
    ) {
      setFieldError("Mỗi phương án cần nhãn, mô tả và hệ quả.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        outstandingTotal: outstandingTotal.trim(),
        periodsInShortfall: periods,
        triggerSummary: triggerSummary.trim(),
        escalationRationale: escalationRationale.trim(),
        evidenceRefs,
        options: cleanedOptions,
      });
      setOutstandingTotal("");
      setPeriodsInShortfall("");
      setTriggerSummary("");
      setEscalationRationale("");
      setEvidence("");
      setOptions([
        { key: `opt-${optionKeySeq++}`, label: "", description: "", consequences: "", dependencies: "" },
      ]);
    } catch (requestError) {
      const details = getRecoveryNotTriggeredDetails(requestError);
      if (details) {
        setNotTriggered(details);
      } else {
        setSubmitError(getSettlementError(requestError));
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={gates.form} noValidate onSubmit={handleSubmit}>
      <div className={gates.fieldRow}>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-total`}>
            Tổng dư nợ <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-total`}
            inputMode="decimal"
            onChange={(event) => setOutstandingTotal(event.target.value)}
            value={outstandingTotal}
          />
        </div>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-periods`}>
            Số kỳ shortfall <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-periods`}
            inputMode="numeric"
            onChange={(event) => setPeriodsInShortfall(event.target.value)}
            value={periodsInShortfall}
          />
        </div>
      </div>
      <div className={gates.field}>
        <label className={gates.fieldLabel} htmlFor={`${formId}-summary`}>
          Tóm tắt trigger <span className={gates.required}>*</span>
        </label>
        <textarea
          className={gates.textarea}
          disabled={pending}
          id={`${formId}-summary`}
          maxLength={4000}
          onChange={(event) => setTriggerSummary(event.target.value)}
          value={triggerSummary}
        />
      </div>
      <div className={gates.field}>
        <label className={gates.fieldLabel} htmlFor={`${formId}-escalation`}>
          Ghi nhận trình cấp có thẩm quyền <span className={gates.required}>*</span>
        </label>
        <textarea
          className={gates.textarea}
          disabled={pending}
          id={`${formId}-escalation`}
          maxLength={4000}
          onChange={(event) => setEscalationRationale(event.target.value)}
          value={escalationRationale}
        />
      </div>
      <div className={gates.field}>
        <label className={gates.fieldLabel} htmlFor={`${formId}-evidence`}>
          Tham chiếu bằng chứng (mỗi dòng một mục) <span className={gates.required}>*</span>
        </label>
        <textarea
          className={gates.textarea}
          disabled={pending}
          id={`${formId}-evidence`}
          onChange={(event) => setEvidence(event.target.value)}
          value={evidence}
        />
      </div>
      <div className={gates.field}>
        <span className={gates.fieldLabel}>
          Phương án xử lý nợ <span className={gates.required}>*</span>
        </span>
        {options.map((option, index) => (
          <div className={styles.optionEditor} key={option.key}>
            <div className={styles.optionEditorHead}>
              <p className={styles.optionEditorTitle}>Phương án {index + 1}</p>
              {options.length > 1 ? (
                <button
                  className={styles.linkButton}
                  disabled={pending}
                  onClick={() => setOptions((prior) => prior.filter((entry) => entry.key !== option.key))}
                  type="button"
                >
                  Xóa
                </button>
              ) : null}
            </div>
            <input
              aria-label={`Nhãn phương án ${index + 1}`}
              className={gates.input}
              disabled={pending}
              onChange={(event) => updateOption(option.key, { label: event.target.value })}
              placeholder="Nhãn phương án"
              value={option.label}
            />
            <textarea
              aria-label={`Mô tả phương án ${index + 1}`}
              className={gates.textarea}
              disabled={pending}
              onChange={(event) => updateOption(option.key, { description: event.target.value })}
              placeholder="Mô tả"
              value={option.description}
            />
            <textarea
              aria-label={`Hệ quả phương án ${index + 1}`}
              className={gates.textarea}
              disabled={pending}
              onChange={(event) => updateOption(option.key, { consequences: event.target.value })}
              placeholder="Hệ quả"
              value={option.consequences}
            />
            <input
              aria-label={`Phụ thuộc phương án ${index + 1} (không bắt buộc)`}
              className={gates.input}
              disabled={pending}
              onChange={(event) => updateOption(option.key, { dependencies: event.target.value })}
              placeholder="Phụ thuộc (không bắt buộc)"
              value={option.dependencies ?? ""}
            />
          </div>
        ))}
        <button
          className={styles.linkButton}
          disabled={pending}
          onClick={() =>
            setOptions((prior) => [
              ...prior,
              {
                key: `opt-${optionKeySeq++}`,
                label: "",
                description: "",
                consequences: "",
                dependencies: "",
              },
            ])
          }
          type="button"
        >
          Thêm phương án
        </button>
      </div>
      {fieldError ? (
        <p className={gates.fieldError} role="alert">
          {fieldError}
        </p>
      ) : null}
      {notTriggered ? (
        <div className={styles.ineligibleBanner} role="alert">
          <p className={styles.ineligibleTitle}>Chưa đủ điều kiện mở hồ sơ xử lý nợ</p>
          <dl className={styles.detailGrid}>
            <DetailItem label="Tổng dư nợ" value={notTriggered.outstandingTotal} />
            <DetailItem label="Số kỳ shortfall" value={String(notTriggered.periodsInShortfall)} />
            <DetailItem label="Ngưỡng số kỳ" value={String(notTriggered.thresholdPeriods)} />
          </dl>
        </div>
      ) : null}
      {submitError ? (
        <div className={gates.submitError} role="alert">
          <p>{submitError}</p>
        </div>
      ) : null}
      <div className={gates.formActions}>
        <button aria-busy={pending} className={gates.submit} disabled={pending} type="submit">
          {pending ? "Đang mở hồ sơ…" : "Mở hồ sơ xử lý nợ"}
        </button>
      </div>
    </form>
  );
}
