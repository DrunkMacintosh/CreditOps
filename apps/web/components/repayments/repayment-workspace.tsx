"use client";

import React, { useCallback, useId, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  CreateFacilityInput,
  CreateNoteInput,
  EVENT_KIND_LABELS,
  EXCEPTION_KIND_LABELS,
  EXCEPTION_KIND_ORDER,
  Facility,
  LedgerSnapshot,
  NOTE_KIND_LABELS,
  PERIOD_STATUS_LABELS,
  RecordEventInput,
  REPAYMENT_STYLE_LABELS,
  RepaymentEvent,
  RepaymentsApiClient,
  formatDate,
  getRepaymentError,
  labelOrUnsupported,
  repaymentsApi,
  shortId,
  type CollectionsException,
  type EventKind,
  type NoteKind,
} from "../../lib/api/repayments";
import { CaseNav } from "../shell/case-nav";
import gates from "../gates/gates.module.css";
import styles from "./repayments.module.css";

type RepaymentsApi = Pick<
  RepaymentsApiClient,
  "createFacility" | "recordEvent" | "getLedger" | "createNote"
>;

// Stage-13 RepaymentLedger workspace. The collections officer opens ONE
// disbursed facility, appends payments / reversals idempotently, reads the
// deterministically recomputed schedule + collections-exception surface, and
// PROPOSES contact actions as free-text notes — nothing executes. Money figures
// are the server's exact Decimal strings, rendered verbatim. No polling.
//
// CONTRACT NOTE: the API exposes no facility/events read endpoint, so the
// facility summary comes from the open response and the events list is the set
// appended in the current session; the ledger recompute supplies the schedule,
// exceptions and notes.
export function RepaymentWorkspace({
  caseId,
  api = repaymentsApi,
}: {
  caseId: string;
  api?: RepaymentsApi;
}) {
  const [facility, setFacility] = useState<Facility | null>(null);
  const [ledger, setLedger] = useState<LedgerSnapshot | null>(null);
  const [events, setEvents] = useState<RepaymentEvent[]>([]);
  const [ledgerLoading, setLedgerLoading] = useState(false);
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);

  const loadLedger = useCallback(
    async (facilityId: string) => {
      setLedgerLoading(true);
      setLedgerError(null);
      setUnauthorized(false);
      try {
        setLedger(await api.getLedger(caseId, facilityId));
      } catch (requestError) {
        if (requestError instanceof ApiClientError && requestError.status === 403) {
          setUnauthorized(true);
        } else {
          setLedgerError(getRepaymentError(requestError));
        }
      } finally {
        setLedgerLoading(false);
      }
    },
    [api, caseId],
  );

  const onFacilityOpened = useCallback(
    (opened: Facility) => {
      setFacility(opened);
      setEvents([]);
      void loadLedger(opened.id);
    },
    [loadLedger],
  );

  const onEventRecorded = useCallback(
    (event: RepaymentEvent) => {
      // Only accumulate genuinely-new rows; a duplicate delivery returns the
      // existing row and must not be listed twice.
      setEvents((prior) =>
        prior.some((existing) => existing.id === event.id) ? prior : [...prior, event],
      );
      if (facility) void loadLedger(facility.id);
    },
    [facility, loadLedger],
  );

  return (
    <>
      <CaseNav caseId={caseId} current="thu-no" />
      <div className="page-heading">
        <p className="eyebrow">Giai đoạn 13 · Thu nợ gốc, lãi và phí</p>
        <h1>Thu nợ</h1>
      </div>

      {facility === null ? (
        <div className={gates.workspace}>
          <div className="empty-state">
            <p className="empty-state-title">Chưa mở khoản vay để theo dõi thu nợ</p>
            <p className="empty-state-hint">
              Mở một khoản vay đã giải ngân để tính lại lịch trả nợ, ghi nhận thanh toán /
              bút toán đảo và đề xuất hành động thu nợ. Khoản vay chỉ mở được khi đã có quyết
              định phê duyệt tín dụng cho phiên bản hồ sơ hiện tại.
            </p>
          </div>
          <section aria-labelledby="open-facility-heading" className={gates.section}>
            <h2 className={gates.sectionTitle} id="open-facility-heading">
              Mở khoản vay
            </h2>
            <OpenFacilityForm
              onSubmit={async (input) => {
                onFacilityOpened(await api.createFacility(caseId, input));
              }}
            />
          </section>
        </div>
      ) : (
        <div className={gates.workspace}>
          <FacilitySummary facility={facility} ledger={ledger} />

          <section aria-labelledby="schedule-heading" className={gates.section}>
            <h2 className={gates.sectionTitle} id="schedule-heading">
              Lịch trả nợ dự kiến và số đã phân bổ
            </h2>
            <LedgerBody
              error={ledgerError}
              ledger={ledger}
              loading={ledgerLoading}
              onRetry={() => void loadLedger(facility.id)}
              unauthorized={unauthorized}
            />
          </section>

          <section aria-labelledby="events-heading" className={gates.section}>
            <h2 className={gates.sectionTitle} id="events-heading">
              Sự kiện thu nợ
            </h2>
            <EventsList events={events} />
            <RecordEventForm
              events={events}
              onSubmit={(input) => api.recordEvent(caseId, facility.id, input)}
              onRecorded={onEventRecorded}
            />
          </section>

          <section aria-labelledby="note-heading" className={gates.section}>
            <h2 className={gates.sectionTitle} id="note-heading">
              Đề xuất hành động thu nợ — chờ cấp có thẩm quyền
            </h2>
            <p className={gates.sectionLead}>
              Ghi chú là đề xuất của con người, không thực thi bất kỳ hành động thu hồi, phong
              tỏa hạn mức hay cơ cấu lại nào.
            </p>
            <CollectionNoteForm
              onSubmit={async (input) => {
                await api.createNote(caseId, facility.id, input);
                await loadLedger(facility.id);
              }}
            />
          </section>
        </div>
      )}
    </>
  );
}

function FacilitySummary({
  facility,
  ledger,
}: {
  facility: Facility;
  ledger: LedgerSnapshot | null;
}) {
  return (
    <section aria-labelledby="facility-heading" className={gates.section}>
      <h2 className={gates.sectionTitle} id="facility-heading">
        Tóm tắt khoản vay
      </h2>
      <div className={styles.chipRow}>
        <span
          className={`status-chip ${
            ledger?.isSettled ? "status-chip--ok" : "status-chip--amber"
          }`}
        >
          {ledger?.isSettled ? "Đã tất toán" : "Còn dư nợ"}
        </span>
        <span className={styles.meta}>Hồ sơ v{facility.caseVersion}</span>
        <span className={styles.meta}>Quyết định nguồn {shortId(facility.decisionId)}</span>
      </div>
      <dl className={styles.summaryGrid}>
        <SummaryItem label="Gốc vay" value={facility.principal} />
        <SummaryItem label="Lãi suất năm (%)" value={facility.annualRatePercent} />
        <SummaryItem label="Kỳ hạn (tháng)" value={String(facility.termMonths)} />
        <SummaryItem label="Phí định kỳ" value={facility.periodicFee} />
        <SummaryItem
          label="Kiểu trả nợ"
          value={labelOrUnsupported(REPAYMENT_STYLE_LABELS, String(facility.repaymentStyle))}
        />
        <SummaryItem label="Kỳ trả đầu tiên" value={formatDate(facility.firstPaymentDate)} />
        {ledger ? (
          <>
            <SummaryItem label="Dư nợ gốc" value={ledger.outstandingPrincipal} />
            <SummaryItem label="Dư lãi" value={ledger.outstandingInterest} />
            <SummaryItem label="Dư phí" value={ledger.outstandingFees} />
            <SummaryItem label="Tổng dư nợ" value={ledger.outstandingTotal} />
            <SummaryItem label="Đã thu ròng" value={ledger.netPaid} />
            <SummaryItem label="Thu vượt" value={ledger.overpayment} />
          </>
        ) : null}
      </dl>
    </section>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.summaryItem}>
      <dt className={styles.summaryLabel}>{label}</dt>
      <dd className={styles.summaryValue}>{value}</dd>
    </div>
  );
}

function LedgerBody({
  ledger,
  loading,
  error,
  unauthorized,
  onRetry,
}: {
  ledger: LedgerSnapshot | null;
  loading: boolean;
  error: string | null;
  unauthorized: boolean;
  onRetry: () => void;
}) {
  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Đang tải sổ thu nợ"
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
      <div className="state-panel" role="alert">
        <p>Bạn không có vai trò tham gia hồ sơ để xem sổ thu nợ.</p>
      </div>
    );
  }
  if (error || !ledger) {
    return (
      <div className="state-panel" role="alert">
        <p>{error ?? "Không thể đọc sổ thu nợ."}</p>
        <button className="button button-secondary" onClick={onRetry} type="button">
          Thử tải lại
        </button>
      </div>
    );
  }
  return (
    <>
      <ScheduleTable ledger={ledger} />
      <ExceptionGroups exceptions={ledger.exceptions} />
      <CollectionNotesList ledger={ledger} />
    </>
  );
}

function ScheduleTable({ ledger }: { ledger: LedgerSnapshot }) {
  if (ledger.periods.length === 0) {
    return (
      <div className="empty-state">
        <p className="empty-state-title">Chưa có kỳ trả nợ nào</p>
        <p className="empty-state-hint">Lịch trả nợ được tính lại từ khoản vay và các sự kiện thu nợ.</p>
      </div>
    );
  }
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption>Số kỳ được tính lại theo chính sách phân bổ {ledger.allocationPolicyVersion}.</caption>
        <thead>
          <tr>
            <th scope="col">Kỳ</th>
            <th scope="col">Đến hạn</th>
            <th className={styles.cellNum} scope="col">Phí dự kiến</th>
            <th className={styles.cellNum} scope="col">Lãi dự kiến</th>
            <th className={styles.cellNum} scope="col">Gốc dự kiến</th>
            <th className={styles.cellNum} scope="col">Phí đã phân bổ</th>
            <th className={styles.cellNum} scope="col">Lãi đã phân bổ</th>
            <th className={styles.cellNum} scope="col">Gốc đã phân bổ</th>
            <th className={styles.cellNum} scope="col">Còn phải thu</th>
            <th scope="col">Trạng thái</th>
          </tr>
        </thead>
        <tbody>
          {ledger.periods.map((period) => (
            <tr className={period.overdue ? styles.rowOverdue : undefined} key={period.period}>
              <td>{period.period}</td>
              <td>{formatDate(period.dueDate)}</td>
              <td className={styles.cellNum}>{period.expectedFee}</td>
              <td className={styles.cellNum}>{period.expectedInterest}</td>
              <td className={styles.cellNum}>{period.expectedPrincipal}</td>
              <td className={styles.cellNum}>{period.allocatedFee}</td>
              <td className={styles.cellNum}>{period.allocatedInterest}</td>
              <td className={styles.cellNum}>{period.allocatedPrincipal}</td>
              <td className={styles.cellNum}>{period.outstandingTotal}</td>
              <td>
                {labelOrUnsupported(PERIOD_STATUS_LABELS, String(period.status))}
                {period.overdue ? " · quá hạn" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExceptionGroups({ exceptions }: { exceptions: CollectionsException[] }) {
  if (exceptions.length === 0) {
    return <p className={gates.sectionLead}>Không có ngoại lệ thu nợ nào được phát hiện.</p>;
  }
  // Group by kind in a stable order; unknown kinds fail closed into their own
  // group with the unsupported label, never silently dropped.
  const known = EXCEPTION_KIND_ORDER.map((kind) => ({
    kind: kind as string,
    label: EXCEPTION_KIND_LABELS[kind],
    items: exceptions.filter((exception) => exception.kind === kind),
  })).filter((group) => group.items.length > 0);
  const unknownItems = exceptions.filter(
    (exception) => !EXCEPTION_KIND_ORDER.includes(exception.kind as never),
  );
  const groups = [...known];
  if (unknownItems.length > 0) {
    groups.push({
      kind: "__unknown__",
      label: labelOrUnsupported(EXCEPTION_KIND_LABELS, "__unknown__"),
      items: unknownItems,
    });
  }

  return (
    <div className={styles.exceptionGroups}>
      {groups.map((group) => (
        <div className={styles.exceptionGroup} key={group.kind}>
          <div className={styles.exceptionGroupHead}>
            <h3 className={styles.exceptionGroupTitle}>{group.label}</h3>
            <span className="status-chip status-chip--amber">{group.items.length} mục</span>
          </div>
          {group.items.map((exception, index) => (
            <div className={styles.exceptionItem} key={`${group.kind}-${index}`}>
              <p className={styles.exceptionDetail}>{exception.detailVi}</p>
              <p className={styles.exceptionMeta}>
                {exception.period !== null ? `Kỳ ${exception.period} · ` : ""}
                Số tiền {exception.amount}
              </p>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function CollectionNotesList({ ledger }: { ledger: LedgerSnapshot }) {
  if (ledger.notes.length === 0) return null;
  return (
    <div>
      <h3 className={styles.exceptionGroupTitle}>Ghi chú thu nợ đã lưu</h3>
      <ul className={gates.list}>
        {ledger.notes.map((note) => (
          <li className={gates.entry} key={note.id}>
            <div className={gates.entryHead}>
              <span className="status-chip status-chip--muted">
                {labelOrUnsupported(NOTE_KIND_LABELS, String(note.noteKind))}
              </span>
              <span className={styles.meta}>{note.authorRole}</span>
            </div>
            <p className={gates.entryText}>{note.noteText}</p>
            {note.proposedAction ? (
              <p className={gates.entryMeta}>Đề xuất hành động: {note.proposedAction}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function EventsList({ events }: { events: RepaymentEvent[] }) {
  if (events.length === 0) {
    return (
      <p className={gates.sectionLead}>
        Chưa ghi nhận sự kiện nào trong phiên làm việc này.
      </p>
    );
  }
  const paymentsByRef = new Map(
    events.filter((event) => event.kind === "PAYMENT").map((event) => [event.id, event]),
  );
  return (
    <ul className={gates.list}>
      {events.map((event) => {
        const linkedPayment = event.reversedEventId
          ? paymentsByRef.get(event.reversedEventId)
          : undefined;
        return (
          <li className={gates.entry} key={event.id}>
            <div className={gates.entryHead}>
              <span className="status-chip status-chip--muted">
                {labelOrUnsupported(EVENT_KIND_LABELS, String(event.kind))}
              </span>
              <span className={styles.meta}>{formatDate(event.effectiveDate)}</span>
            </div>
            <p className={gates.entryText}>Số tiền {event.amount}</p>
            <p className={gates.entryMeta}>Tham chiếu: {event.externalReference}</p>
            {event.reversedEventId ? (
              <p className={styles.linkage}>
                Đảo khoản thanh toán {shortId(event.reversedEventId)}
                {linkedPayment ? ` (tham chiếu ${linkedPayment.externalReference})` : ""}
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function OpenFacilityForm({ onSubmit }: { onSubmit: (input: CreateFacilityInput) => Promise<void> }) {
  const formId = useId();
  const [principal, setPrincipal] = useState("");
  const [rate, setRate] = useState("");
  const [term, setTerm] = useState("");
  const [style, setStyle] = useState<"" | "EQUAL_PRINCIPAL" | "BALLOON">("");
  const [firstPayment, setFirstPayment] = useState("");
  const [fee, setFee] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const termMonths = Number.parseInt(term, 10);
    if (
      principal.trim().length === 0 ||
      rate.trim().length === 0 ||
      Number.isNaN(termMonths) ||
      termMonths < 1 ||
      style === "" ||
      firstPayment.length === 0
    ) {
      setFieldError("Nhập đầy đủ gốc vay, lãi suất, kỳ hạn, kiểu trả nợ và kỳ trả đầu tiên.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        principal: principal.trim(),
        annualRatePercent: rate.trim(),
        termMonths,
        repaymentStyle: style,
        firstPaymentDate: firstPayment,
        periodicFee: fee.trim() || undefined,
      });
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getRepaymentError(requestError));
      setPending(false);
    }
  };

  return (
    <form className={gates.form} noValidate onSubmit={handleSubmit}>
      <div className={gates.fieldRow}>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-principal`}>
            Gốc vay <span className={gates.required}>*</span>
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
          <label className={gates.fieldLabel} htmlFor={`${formId}-rate`}>
            Lãi suất năm (%) <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-rate`}
            inputMode="decimal"
            onChange={(event) => setRate(event.target.value)}
            value={rate}
          />
        </div>
      </div>
      <div className={gates.fieldRow}>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-term`}>
            Kỳ hạn (tháng) <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-term`}
            inputMode="numeric"
            onChange={(event) => setTerm(event.target.value)}
            value={term}
          />
        </div>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-fee`}>
            Phí định kỳ (không bắt buộc)
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-fee`}
            inputMode="decimal"
            onChange={(event) => setFee(event.target.value)}
            value={fee}
          />
        </div>
      </div>
      <div className={gates.fieldRow}>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-style`}>
            Kiểu trả nợ <span className={gates.required}>*</span>
          </label>
          <select
            className={gates.select}
            disabled={pending}
            id={`${formId}-style`}
            onChange={(event) => setStyle(event.target.value as typeof style)}
            value={style}
          >
            <option value="">— Chọn kiểu trả nợ —</option>
            <option value="EQUAL_PRINCIPAL">{REPAYMENT_STYLE_LABELS.EQUAL_PRINCIPAL}</option>
            <option value="BALLOON">{REPAYMENT_STYLE_LABELS.BALLOON}</option>
          </select>
        </div>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${formId}-first`}>
            Kỳ trả đầu tiên <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${formId}-first`}
            onChange={(event) => setFirstPayment(event.target.value)}
            type="date"
            value={firstPayment}
          />
        </div>
      </div>
      {fieldError ? (
        <p className={gates.fieldError} role="alert">
          {fieldError}
        </p>
      ) : null}
      {submitError ? (
        <div className={gates.submitError} role="alert">
          <p>{submitError}</p>
          {staleReload ? (
            <button className="button button-secondary" onClick={() => window.location.reload()} type="button">
              Tải lại
            </button>
          ) : null}
        </div>
      ) : null}
      <div className={gates.formActions}>
        <button aria-busy={pending} className={gates.submit} disabled={pending} type="submit">
          {pending ? "Đang mở khoản vay…" : "Mở khoản vay giải ngân"}
        </button>
      </div>
    </form>
  );
}

function RecordEventForm({
  events,
  onSubmit,
  onRecorded,
}: {
  events: RepaymentEvent[];
  onSubmit: (input: RecordEventInput) => Promise<RepaymentEvent>;
  onRecorded: (event: RepaymentEvent) => void;
}) {
  const groupName = useId();
  const [kind, setKind] = useState<EventKind | "">("");
  const [amount, setAmount] = useState("");
  const [externalReference, setExternalReference] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [reversedEventId, setReversedEventId] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<{ duplicate: boolean; reference: string } | null>(null);

  const payments = events.filter((event) => event.kind === "PAYMENT");

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setResult(null);
    if (kind === "") {
      setFieldError("Chọn loại sự kiện: thanh toán hoặc bút toán đảo.");
      return;
    }
    if (amount.trim().length === 0) {
      setFieldError("Nhập số tiền của sự kiện.");
      return;
    }
    if (externalReference.trim().length === 0) {
      setFieldError("Tham chiếu ngoài là bắt buộc để bảo đảm tính idempotent.");
      return;
    }
    if (effectiveDate.length === 0) {
      setFieldError("Nhập ngày hiệu lực của sự kiện.");
      return;
    }
    if (kind === "REVERSAL" && reversedEventId === "") {
      setFieldError("Bút toán đảo phải tham chiếu một khoản thanh toán đã ghi.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      const recorded = await onSubmit({
        kind,
        amount: amount.trim(),
        externalReference: externalReference.trim(),
        effectiveDate,
        reversedEventId: kind === "REVERSAL" ? reversedEventId : undefined,
      });
      onRecorded(recorded);
      setResult({ duplicate: !recorded.created, reference: recorded.externalReference });
      setKind("");
      setAmount("");
      setExternalReference("");
      setEffectiveDate("");
      setReversedEventId("");
    } catch (requestError) {
      setSubmitError(getRepaymentError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={gates.form} noValidate onSubmit={handleSubmit}>
      <div className={gates.field}>
        <span className={gates.fieldLabel}>
          Loại sự kiện <span className={gates.required}>*</span>
        </span>
        <div className={gates.radioGroup} role="radiogroup" aria-label="Loại sự kiện thu nợ">
          {(["PAYMENT", "REVERSAL"] as const).map((option) => (
            <label
              className={gates.radioOption}
              data-checked={kind === option ? "true" : "false"}
              key={option}
            >
              <input
                checked={kind === option}
                disabled={pending}
                name={groupName}
                onChange={() => {
                  setKind(option);
                  if (fieldError) setFieldError(null);
                }}
                type="radio"
                value={option}
              />
              <span>{EVENT_KIND_LABELS[option]}</span>
            </label>
          ))}
        </div>
      </div>
      <div className={gates.fieldRow}>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${groupName}-amount`}>
            Số tiền <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${groupName}-amount`}
            inputMode="decimal"
            onChange={(event) => {
              setAmount(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            value={amount}
          />
        </div>
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${groupName}-date`}>
            Ngày hiệu lực <span className={gates.required}>*</span>
          </label>
          <input
            className={gates.input}
            disabled={pending}
            id={`${groupName}-date`}
            onChange={(event) => setEffectiveDate(event.target.value)}
            type="date"
            value={effectiveDate}
          />
        </div>
      </div>
      <div className={gates.field}>
        <label className={gates.fieldLabel} htmlFor={`${groupName}-ref`}>
          Tham chiếu ngoài <span className={gates.required}>*</span>
        </label>
        <input
          className={gates.input}
          disabled={pending}
          id={`${groupName}-ref`}
          maxLength={200}
          onChange={(event) => {
            setExternalReference(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={externalReference}
        />
      </div>
      {kind === "REVERSAL" ? (
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${groupName}-reversed`}>
            Khoản thanh toán bị đảo <span className={gates.required}>*</span>
          </label>
          <select
            className={gates.select}
            disabled={pending}
            id={`${groupName}-reversed`}
            onChange={(event) => setReversedEventId(event.target.value)}
            value={reversedEventId}
          >
            <option value="">— Chọn khoản thanh toán —</option>
            {payments.map((payment) => (
              <option key={payment.id} value={payment.id}>
                {payment.externalReference} · {payment.amount}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {fieldError ? (
        <p className={gates.fieldError} role="alert">
          {fieldError}
        </p>
      ) : null}
      {submitError ? (
        <div className={gates.submitError} role="alert">
          <p>{submitError}</p>
        </div>
      ) : null}
      {result ? (
        <p
          className={`${styles.resultNote} ${
            result.duplicate ? styles.resultNoteDuplicate : styles.resultNoteNew
          }`}
          role="status"
        >
          {result.duplicate
            ? `Sự kiện đã tồn tại — tham chiếu ${result.reference} đã được ghi trước đó, không tạo bản ghi mới.`
            : `Đã ghi sự kiện mới — tham chiếu ${result.reference}.`}
        </p>
      ) : null}
      <div className={gates.formActions}>
        <button aria-busy={pending} className={gates.submit} disabled={pending} type="submit">
          {pending ? "Đang ghi sự kiện…" : "Ghi nhận sự kiện thu nợ"}
        </button>
      </div>
    </form>
  );
}

function CollectionNoteForm({ onSubmit }: { onSubmit: (input: CreateNoteInput) => Promise<void> }) {
  const groupName = useId();
  const [noteKind, setNoteKind] = useState<NoteKind | "">("");
  const [noteText, setNoteText] = useState("");
  const [proposedAction, setProposedAction] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    if (noteKind === "") {
      setFieldError("Chọn loại ghi chú: quan sát hoặc đề xuất hành động.");
      return;
    }
    if (noteText.trim().length === 0) {
      setFieldError("Nhập nội dung ghi chú.");
      return;
    }
    if (noteKind === "PROPOSED_ACTION" && proposedAction.trim().length === 0) {
      setFieldError("Đề xuất hành động phải nêu rõ hành động được đề xuất.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        noteKind,
        noteText: noteText.trim(),
        proposedAction:
          noteKind === "PROPOSED_ACTION" ? proposedAction.trim() : undefined,
      });
      setNoteKind("");
      setNoteText("");
      setProposedAction("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getRepaymentError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={gates.form} noValidate onSubmit={handleSubmit}>
      <div className={gates.field}>
        <span className={gates.fieldLabel}>
          Loại ghi chú <span className={gates.required}>*</span>
        </span>
        <div className={gates.radioGroup} role="radiogroup" aria-label="Loại ghi chú thu nợ">
          {(["OBSERVATION", "PROPOSED_ACTION"] as const).map((option) => (
            <label
              className={gates.radioOption}
              data-checked={noteKind === option ? "true" : "false"}
              key={option}
            >
              <input
                checked={noteKind === option}
                disabled={pending}
                name={groupName}
                onChange={() => {
                  setNoteKind(option);
                  if (fieldError) setFieldError(null);
                }}
                type="radio"
                value={option}
              />
              <span>{NOTE_KIND_LABELS[option]}</span>
            </label>
          ))}
        </div>
      </div>
      <div className={gates.field}>
        <label className={gates.fieldLabel} htmlFor={`${groupName}-text`}>
          Nội dung ghi chú <span className={gates.required}>*</span>
        </label>
        <textarea
          className={gates.textarea}
          disabled={pending}
          id={`${groupName}-text`}
          maxLength={4000}
          onChange={(event) => {
            setNoteText(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={noteText}
        />
      </div>
      {noteKind === "PROPOSED_ACTION" ? (
        <div className={gates.field}>
          <label className={gates.fieldLabel} htmlFor={`${groupName}-action`}>
            Hành động đề xuất <span className={gates.required}>*</span>
          </label>
          <textarea
            className={gates.textarea}
            disabled={pending}
            id={`${groupName}-action`}
            maxLength={400}
            onChange={(event) => {
              setProposedAction(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            value={proposedAction}
          />
        </div>
      ) : null}
      {fieldError ? (
        <p className={gates.fieldError} role="alert">
          {fieldError}
        </p>
      ) : null}
      {submitError ? (
        <div className={gates.submitError} role="alert">
          <p>{submitError}</p>
          {staleReload ? (
            <button className="button button-secondary" onClick={() => window.location.reload()} type="button">
              Tải lại
            </button>
          ) : null}
        </div>
      ) : null}
      <div className={gates.formActions}>
        <button aria-busy={pending} className={gates.submit} disabled={pending} type="submit">
          {pending ? "Đang lưu ghi chú…" : "Lưu đề xuất hành động thu nợ"}
        </button>
      </div>
    </form>
  );
}
