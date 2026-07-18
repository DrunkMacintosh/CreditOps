"use client";

import React, { useCallback, useEffect, useId, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  DisbursementsApiClient,
  disbursementsApi,
  EXECUTION_STATUS_LABELS,
  formatAmount,
  formatDateTime,
  GATE_STATUS_LABELS,
  getDisbursementError,
  isUnresolvedExecution,
  labelOrUnsupported,
  RECONCILIATION_OUTCOME_LABELS,
  RECONCILIATION_OUTCOMES,
  shortId,
  type CreateDisbursementInput,
  type DisbursementActionDetail,
  type DisbursementList,
  type ExecutionReceipt,
  type ReconciliationOutcome,
} from "../../lib/api/disbursements";
import { RecordActionForm } from "../gates/record-action-form";
import { CaseNav } from "../shell/case-nav";
import styles from "../gates/gates.module.css";

type DisbursementsApi = Pick<
  DisbursementsApiClient,
  "list" | "create" | "validate" | "authorize" | "execute" | "reconcile"
>;

// Stage-11 proposed-disbursement workspace. Credit Operations only PREPARES the
// action; execution runs a labelled deterministic MOCK after TWO separate human
// gates satisfied by DIFFERENT actors. An EXECUTION_UNKNOWN / EXECUTION_REQUESTED
// result is rendered as a distinct BLOCKING state whose only forward move is a
// human reconciliation — never a blind retry. No polling; refresh is manual.
export function DisbursementWorkspace({
  caseId,
  api = disbursementsApi,
}: {
  caseId: string;
  api?: DisbursementsApi;
}) {
  const [list, setList] = useState<DisbursementList | null>(null);
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
      setList(await api.list(caseId));
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 403) {
        setUnauthorized(true);
      } else {
        setError(getDisbursementError(requestError));
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
      setList(await api.list(caseId));
    } catch (requestError) {
      setRefreshError(getDisbursementError(requestError));
    }
  }, [api, caseId]);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Đang tải hành động giải ngân"
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
        <CaseNav caseId={caseId} current="giai-ngan" />
        <div className="state-panel" role="alert">
          <p>Bạn không có vai trò tham gia hồ sơ để xem hành động giải ngân.</p>
        </div>
      </>
    );
  }

  if (error || !list) {
    return (
      <>
        <CaseNav caseId={caseId} current="giai-ngan" />
        <div className="state-panel" role="alert">
          <p>{error ?? "Không thể đọc danh sách hành động giải ngân."}</p>
          <button className="button button-secondary" onClick={() => void load()} type="button">
            Thử tải lại
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <CaseNav caseId={caseId} current="giai-ngan" />
      <div className="page-heading">
        <p className="eyebrow">Giai đoạn 11 · Giải ngân vốn vay</p>
        <h1>Giải ngân vốn vay</h1>
      </div>

      <div className={styles.workspace}>
        <p className={styles.disclaimer}>
          Nghiệp vụ Vận hành tín dụng chỉ CHUẨN BỊ hành động; việc thực thi chạy qua bộ
          thực thi mô phỏng (nhãn &quot;Thực thi mô phỏng&quot;) sau HAI cổng kiểm soát do
          hai người khác nhau thực hiện. Không có giao dịch nào chạm vào hệ thống lõi thật.
        </p>

        <div className={styles.gateRow}>
          <span className={styles.meta}>Hồ sơ v{list.caseVersion}</span>
        </div>

        {list.actions.length === 0 ? (
          <div className="empty-state">
            <p className="empty-state-title">Chưa có hành động giải ngân nào</p>
            <p className="empty-state-hint">
              Hành động giải ngân chỉ mở được sau khi có quyết định phê duyệt tín dụng VÀ cổng
              điều kiện giải ngân đã được xác nhận cho phiên bản hồ sơ hiện tại.
            </p>
          </div>
        ) : (
          <ul className={styles.list}>
            {list.actions.map((detail) => (
              <ActionCard
                api={api}
                caseId={caseId}
                detail={detail}
                key={detail.action.id}
                onChanged={() => void refresh()}
              />
            ))}
          </ul>
        )}

        <section aria-labelledby="create-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="create-heading">
            Tạo hành động giải ngân đề xuất
          </h2>
          <p className={styles.sectionLead}>
            Cán bộ nghiệp vụ (maker) tạo MỘT hành động đề xuất phái sinh từ điều khoản đã duyệt.
            Số tiền là số thập phân chính xác; để trống để dùng đúng số tiền đã được phê duyệt.
          </p>
          <CreateDisbursementForm
            onReload={() => void refresh()}
            onSubmit={async (input) => {
              await api.create(caseId, input);
              await refresh();
            }}
          />
        </section>

        {refreshError ? (
          <div className="state-panel" role="alert">
            <p>Đã ghi nhận, nhưng không tải lại được bản mới nhất: {refreshError}</p>
            <button className="button button-secondary" onClick={() => void refresh()} type="button">
              Tải lại
            </button>
          </div>
        ) : null}
      </div>
    </>
  );
}

function ActionCard({
  caseId,
  api,
  detail,
  onChanged,
}: {
  caseId: string;
  api: DisbursementsApi;
  detail: DisbursementActionDetail;
  onChanged: () => void;
}) {
  const { action, receipts } = detail;
  const status = String(action.status);
  const validated = detail.validatedGateStatus === "SATISFIED";
  const authorized = detail.authorizedGateStatus === "SATISFIED";
  const unresolved = isUnresolvedExecution(status);
  const executed = status === "CONFIRMED_EXECUTED";

  return (
    <li className={styles.entry}>
      <div className={styles.entryHead}>
        <p className={styles.entryTitle}>
          {formatAmount(action.amount, action.currency)}
        </p>
        <span className="status-chip status-chip--muted">
          {labelOrUnsupported(EXECUTION_STATUS_LABELS, status)}
        </span>
      </div>
      <p className={styles.entryMeta}>
        Thụ hưởng: {action.beneficiaryRef} · Tài khoản: {action.accountRef}
      </p>
      <p className={styles.entryMeta}>
        Quyết định nguồn {shortId(action.decisionId)} · Người tạo {shortId(action.createdBy)} ·{" "}
        {formatDateTime(action.createdAt)}
      </p>

      <div className={styles.gateRow}>
        <span className={`status-chip ${validated ? "status-chip--ok" : "status-chip--amber"}`}>
          Kiểm tra: {GATE_STATUS_LABELS[validated ? "SATISFIED" : "OPEN"]}
        </span>
        <span className={`status-chip ${authorized ? "status-chip--ok" : "status-chip--amber"}`}>
          Uỷ quyền: {GATE_STATUS_LABELS[authorized ? "SATISFIED" : "OPEN"]}
        </span>
      </div>

      {unresolved ? (
        <BlockingReconcileSection
          action={action}
          api={api}
          caseId={caseId}
          onChanged={onChanged}
          status={status}
        />
      ) : (
        <>
          <GateSection
            api={api}
            caseId={caseId}
            actionId={action.id}
            gate="validate"
            heading="Xác nhận kiểm tra giải ngân"
            lead="Người kiểm soát xác nhận đã kiểm tra hành động giải ngân (cổng HG_DISBURSEMENT_VALIDATED)."
            hint="Không nhập lý do; máy chủ kiểm tra fail-closed."
            submitLabel="Xác nhận đã kiểm tra giải ngân"
            satisfied={validated}
            satisfiedNote="Đã xác nhận kiểm tra giải ngân."
            onChanged={onChanged}
          />
          <GateSection
            api={api}
            caseId={caseId}
            actionId={action.id}
            gate="authorize"
            heading="Uỷ quyền hành động đề xuất"
            lead="Một người kiểm soát KHÁC uỷ quyền hành động (cổng HG_DISBURSEMENT_AUTHORIZED). Người uỷ quyền phải khác người đã kiểm tra."
            hint="Không nhập lý do; máy chủ kiểm tra tách biệt nhiệm vụ và trả về 409 khi chưa kiểm tra hoặc cùng một người."
            submitLabel="Uỷ quyền hành động giải ngân"
            satisfied={authorized}
            satisfiedNote="Đã uỷ quyền hành động giải ngân."
            onChanged={onChanged}
          />
          {executed ? (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>Thực thi giải ngân (mô phỏng)</h3>
              <p className={styles.sectionLead}>
                Hành động đã được xác nhận thực thi. Xem biên nhận mô phỏng bên dưới.
              </p>
            </div>
          ) : (
            <ExecuteSection
              api={api}
              caseId={caseId}
              actionId={action.id}
              onChanged={onChanged}
            />
          )}
        </>
      )}

      <ReceiptsList receipts={receipts} />
    </li>
  );
}

function GateSection({
  caseId,
  api,
  actionId,
  gate,
  heading,
  lead,
  hint,
  submitLabel,
  satisfied,
  satisfiedNote,
  onChanged,
}: {
  caseId: string;
  api: DisbursementsApi;
  actionId: string;
  gate: "validate" | "authorize";
  heading: string;
  lead: string;
  hint: string;
  submitLabel: string;
  satisfied: boolean;
  satisfiedNote: string;
  onChanged: () => void;
}) {
  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>{heading}</h3>
      <p className={styles.sectionLead}>{lead}</p>
      {satisfied ? (
        <p className={styles.entryMeta}>{satisfiedNote}</p>
      ) : (
        <RecordActionForm
          formatError={getDisbursementError}
          hint={hint}
          onReload={onChanged}
          onSubmit={async () => {
            if (gate === "validate") {
              await api.validate(caseId, actionId);
            } else {
              await api.authorize(caseId, actionId);
            }
            onChanged();
          }}
          showRationale={false}
          submitLabel={submitLabel}
        />
      )}
    </section>
  );
}

function ExecuteSection({
  caseId,
  api,
  actionId,
  onChanged,
}: {
  caseId: string;
  api: DisbursementsApi;
  actionId: string;
  onChanged: () => void;
}) {
  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>Thực thi giải ngân (mô phỏng)</h3>
      <p className={styles.sectionLead}>
        Chạy bộ thực thi mô phỏng. Yêu cầu CẢ HAI cổng đã đạt và người thực thi phải khác
        người đã tạo hành động. Máy chủ trả về 409 trung thực khi chưa đủ điều kiện, cùng
        người, đã thực thi, hoặc cần đối soát — thông báo được hiển thị nguyên trạng.
      </p>
      <RecordActionForm
        formatError={getDisbursementError}
        hint="Không nhập lý do; đây là thực thi mô phỏng có nhãn, không có giao dịch thật."
        onReload={onChanged}
        onSubmit={async () => {
          await api.execute(caseId, actionId);
          onChanged();
        }}
        pendingLabel="Đang thực thi mô phỏng…"
        showRationale={false}
        submitLabel="Thực thi giải ngân (mô phỏng)"
      />
    </section>
  );
}

// The distinct BLOCKING state: an unresolved execution (EXECUTION_UNKNOWN or a
// stranded EXECUTION_REQUESTED). No retry is offered — the ONLY forward move is a
// human reconciliation with a required outcome (never preselected) and rationale.
function BlockingReconcileSection({
  caseId,
  api,
  action,
  status,
  onChanged,
}: {
  caseId: string;
  api: DisbursementsApi;
  action: { id: string };
  status: string;
  onChanged: () => void;
}) {
  return (
    <section aria-labelledby={`reconcile-${action.id}`} className={styles.blockedBanner}>
      <p className={styles.blockedTitle} id={`reconcile-${action.id}`}>
        Kết quả thực thi chưa xác định — cần đối soát thủ công
      </p>
      <p>
        {status === "EXECUTION_UNKNOWN"
          ? "Bộ thực thi mô phỏng trả về kết quả không xác định: chưa biết tiền có chuyển hay không."
          : "Một lần thực thi đã được ghi nhận nhưng phản hồi bị mất. Trạng thái bị chặn cho tới khi đối soát."}
        {" "}Không tự động thực thi lại; chỉ con người mới được giải quyết.
      </p>
      <ReconcileForm
        onReload={onChanged}
        onSubmit={async (outcome, rationale) => {
          await api.reconcile(caseId, action.id, { outcome, rationale });
          onChanged();
        }}
      />
    </section>
  );
}

function ReconcileForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (outcome: ReconciliationOutcome, rationale: string) => Promise<void>;
  onReload: () => void;
}) {
  const groupName = useId();
  const [outcome, setOutcome] = useState<ReconciliationOutcome | "">("");
  const [rationale, setRationale] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    if (!outcome) {
      setFieldError("Chọn kết quả đối soát trước khi ghi.");
      return;
    }
    const note = rationale.trim();
    if (note.length === 0) {
      setFieldError("Đối soát là quyết định có thẩm quyền: bắt buộc nhập lý do.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit(outcome, note);
      setOutcome("");
      setRationale("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getDisbursementError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <h4 className={styles.formHeading}>Ghi nhận kết quả đối soát</h4>
      <div className={styles.field}>
        <span className={styles.fieldLabel}>
          Kết quả đối soát <span className={styles.required}>*</span>
        </span>
        <div aria-label="Kết quả đối soát" className={styles.radioGroup} role="radiogroup">
          {RECONCILIATION_OUTCOMES.map((option) => (
            <label
              className={styles.radioOption}
              data-checked={outcome === option ? "true" : "false"}
              key={option}
            >
              <input
                checked={outcome === option}
                disabled={pending}
                name={groupName}
                onChange={() => {
                  setOutcome(option);
                  if (fieldError) setFieldError(null);
                }}
                type="radio"
                value={option}
              />
              <span>{RECONCILIATION_OUTCOME_LABELS[option]}</span>
            </label>
          ))}
        </div>
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${groupName}-rationale`}>
          Lý do <span className={styles.required}>*</span>
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
          {pending ? "Đang ghi đối soát…" : "Ghi nhận kết quả đối soát"}
        </button>
      </div>
    </form>
  );
}

function ReceiptsList({ receipts }: { receipts: ExecutionReceipt[] }) {
  if (receipts.length === 0) return null;
  return (
    <section aria-label="Biên nhận thực thi" className={styles.items}>
      {receipts.map((receipt) => (
        <div className={styles.item} key={receipt.id}>
          <div className={styles.entryHead}>
            <span className="status-chip status-chip--muted">Thực thi mô phỏng</span>
            <span className={styles.meta}>
              {labelOrUnsupported(EXECUTION_STATUS_LABELS, String(receipt.resultStatus))}
            </span>
          </div>
          <p className={styles.entryMeta}>
            Biên nhận: {receipt.receiptRef ?? "—"} · Bộ thực thi: {receipt.adapterLabel}
          </p>
          <p className={styles.entryMeta}>
            Khoá idempotency: {shortId(receipt.idempotencyKey)} · Người ghi{" "}
            {shortId(receipt.recordedBy)} · {formatDateTime(receipt.createdAt)}
          </p>
        </div>
      ))}
    </section>
  );
}

function CreateDisbursementForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: CreateDisbursementInput) => Promise<void>;
  onReload: () => void;
}) {
  const formId = useId();
  const [beneficiaryRef, setBeneficiaryRef] = useState("");
  const [accountRef, setAccountRef] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const beneficiary = beneficiaryRef.trim();
    const account = accountRef.trim();
    if (beneficiary.length === 0 || account.length === 0) {
      setFieldError("Nhập tham chiếu thụ hưởng và tài khoản (dữ liệu tổng hợp).");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        beneficiaryRef: beneficiary,
        accountRef: account,
        amount: amount.trim() || undefined,
        currency: currency.trim() || undefined,
      });
      setBeneficiaryRef("");
      setAccountRef("");
      setAmount("");
      setCurrency("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getDisbursementError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-beneficiary`}>
          Tham chiếu thụ hưởng <span className={styles.required}>*</span>
        </label>
        <input
          className={styles.input}
          disabled={pending}
          id={`${formId}-beneficiary`}
          maxLength={400}
          onChange={(event) => {
            setBeneficiaryRef(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={beneficiaryRef}
        />
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-account`}>
          Tham chiếu tài khoản <span className={styles.required}>*</span>
        </label>
        <input
          className={styles.input}
          disabled={pending}
          id={`${formId}-account`}
          maxLength={400}
          onChange={(event) => {
            setAccountRef(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={accountRef}
        />
      </div>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-amount`}>
            Số tiền (không bắt buộc — mặc định theo số đã duyệt)
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-amount`}
            inputMode="decimal"
            maxLength={40}
            onChange={(event) => setAmount(event.target.value)}
            placeholder="Ví dụ: 5000000000.00"
            value={amount}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-currency`}>
            Loại tiền (không bắt buộc)
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-currency`}
            maxLength={8}
            onChange={(event) => setCurrency(event.target.value)}
            placeholder="VND"
            value={currency}
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
          {pending ? "Đang tạo hành động…" : "Tạo hành động giải ngân"}
        </button>
      </div>
    </form>
  );
}
