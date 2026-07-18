"use client";

import React, { useCallback, useEffect, useId, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  ALERT_RULE_LABELS,
  ALERT_STATUS_LABELS,
  ALERT_TRANSITION_LABELS,
  allowedAlertTransitions,
  COMPARISON_OPERATOR_LABELS,
  COMPARISON_OPERATOR_SYMBOLS,
  COMPARISON_OPERATORS,
  formatDate,
  formatDateTime,
  getMonitoringError,
  groupAlertsByStatus,
  labelOrUnsupported,
  MonitoringApiClient,
  monitoringApi,
  OBLIGATION_FREQUENCIES,
  OBLIGATION_FREQUENCY_LABELS,
  shortId,
  type Alert,
  type AlertStatus,
  type ComparisonOperator,
  type Covenant,
  type CovenantTest,
  type CreateCovenantInput,
  type CreateObligationsInput,
  type CreateObservationInput,
  type Obligation,
  type ObligationFrequency,
  type Observation,
  type RunCovenantTestInput,
} from "../../lib/api/monitoring";
import { CaseNav } from "../shell/case-nav";
import styles from "../gates/gates.module.css";

type MonitoringApi = Pick<
  MonitoringApiClient,
  | "listObligations"
  | "createObligations"
  | "listObservations"
  | "recordObservation"
  | "listCovenants"
  | "createCovenant"
  | "runCovenantTest"
  | "listCovenantTests"
  | "listAlerts"
  | "disposeAlert"
>;

interface MonitoringData {
  obligations: Obligation[];
  observations: Observation[];
  covenants: Covenant[];
  covenantTests: CovenantTest[];
  alerts: Alert[];
  caseVersion: number;
}

function splitRefs(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

// Stage-12 post-credit monitoring workspace. All state is written by authorised
// humans; every early-warning alert is raised by a DETERMINISTIC rule (never a
// model). No debt classification anywhere. Alert dispositions move along a closed
// lifecycle; an unknown enum fails closed. No polling; refresh is manual.
export function MonitoringWorkspace({
  caseId,
  api = monitoringApi,
}: {
  caseId: string;
  api?: MonitoringApi;
}) {
  const [data, setData] = useState<MonitoringData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const fetchAll = useCallback(async (): Promise<MonitoringData> => {
    const [obligations, observations, covenants, covenantTests, alerts] = await Promise.all([
      api.listObligations(caseId),
      api.listObservations(caseId),
      api.listCovenants(caseId),
      api.listCovenantTests(caseId),
      api.listAlerts(caseId),
    ]);
    return {
      obligations: obligations.obligations,
      observations: observations.observations,
      covenants: covenants.covenants,
      covenantTests: covenantTests.tests,
      alerts: alerts.alerts,
      caseVersion: alerts.caseVersion,
    };
  }, [api, caseId]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setUnauthorized(false);
    setRefreshError(null);
    try {
      setData(await fetchAll());
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 403) {
        setUnauthorized(true);
      } else {
        setError(getMonitoringError(requestError));
      }
    } finally {
      setLoading(false);
    }
  }, [fetchAll]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(async () => {
    setRefreshError(null);
    try {
      setData(await fetchAll());
    } catch (requestError) {
      setRefreshError(getMonitoringError(requestError));
    }
  }, [fetchAll]);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Đang tải dữ liệu giám sát"
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
        <CaseNav caseId={caseId} current="giam-sat" />
        <div className="state-panel" role="alert">
          <p>Bạn không có vai trò tham gia hồ sơ để xem dữ liệu giám sát.</p>
        </div>
      </>
    );
  }

  if (error || !data) {
    return (
      <>
        <CaseNav caseId={caseId} current="giam-sat" />
        <div className="state-panel" role="alert">
          <p>{error ?? "Không thể đọc dữ liệu giám sát sau cấp tín dụng."}</p>
          <button className="button button-secondary" onClick={() => void load()} type="button">
            Thử tải lại
          </button>
        </div>
      </>
    );
  }

  const alertGroups = groupAlertsByStatus(data.alerts);

  return (
    <>
      <CaseNav caseId={caseId} current="giam-sat" />
      <div className="page-heading">
        <p className="eyebrow">Giai đoạn 12 · Quản lý và giám sát sau cấp tín dụng</p>
        <h1>Giám sát sau cấp tín dụng</h1>
      </div>

      <div className={styles.workspace}>
        <p className={styles.disclaimer}>
          Mọi cảnh báo sớm đều do quy tắc XÁC ĐỊNH (deterministic) phát ra, không phải mô
          hình. Giai đoạn này không phân loại nợ. Con người ghi nhận và xử lý mọi dữ liệu.
        </p>
        <div className={styles.gateRow}>
          <span className={styles.meta}>Hồ sơ v{data.caseVersion}</span>
        </div>

        {/* --- Early-warning alerts (grouped by status) --- */}
        <section aria-labelledby="alerts-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="alerts-heading">
            Cảnh báo sớm
          </h2>
          {data.alerts.length === 0 ? (
            <p className={styles.sectionLead}>Chưa có cảnh báo sớm nào cho phiên bản hồ sơ này.</p>
          ) : (
            alertGroups.map((group) => (
              <div key={group.status}>
                <p className={styles.entryMeta}>
                  {labelOrUnsupported(ALERT_STATUS_LABELS, group.status)} · {group.alerts.length}
                </p>
                <ul className={styles.list}>
                  {group.alerts.map((alert) => (
                    <AlertCard
                      alert={alert}
                      api={api}
                      caseId={caseId}
                      key={alert.id}
                      onChanged={() => void refresh()}
                    />
                  ))}
                </ul>
              </div>
            ))
          )}
        </section>

        {/* --- Monitoring obligations --- */}
        <section aria-labelledby="obligations-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="obligations-heading">
            Nghĩa vụ giám sát
          </h2>
          {data.obligations.length === 0 ? (
            <p className={styles.sectionLead}>Chưa có nghĩa vụ giám sát nào.</p>
          ) : (
            <ul className={styles.list}>
              {data.obligations.map((obligation) => (
                <li className={styles.entry} key={obligation.id}>
                  <div className={styles.entryHead}>
                    <p className={styles.entryText}>
                      Kỳ {obligation.sequence}: {obligation.requirementText}
                    </p>
                    <span className="status-chip status-chip--muted">
                      {labelOrUnsupported(
                        OBLIGATION_FREQUENCY_LABELS,
                        String(obligation.frequency),
                      )}
                    </span>
                  </div>
                  <p className={styles.entryMeta}>Hạn: {formatDate(obligation.dueDate)}</p>
                </li>
              ))}
            </ul>
          )}
          <CreateObligationsForm
            onReload={() => void refresh()}
            onSubmit={async (input) => {
              await api.createObligations(caseId, input);
              await refresh();
            }}
          />
        </section>

        {/* --- Longitudinal observations (three distinct timestamps) --- */}
        <section aria-labelledby="observations-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="observations-heading">
            Quan sát dọc theo thời gian
          </h2>
          {data.observations.length === 0 ? (
            <p className={styles.sectionLead}>Chưa ghi nhận quan sát nào.</p>
          ) : (
            <ul className={styles.list}>
              {data.observations.map((observation) => (
                <ObservationCard key={observation.id} observation={observation} />
              ))}
            </ul>
          )}
          <CreateObservationForm
            obligations={data.obligations}
            onReload={() => void refresh()}
            onSubmit={async (input) => {
              await api.recordObservation(caseId, input);
              await refresh();
            }}
          />
        </section>

        {/* --- Covenants + their echoed-arithmetic tests --- */}
        <section aria-labelledby="covenants-heading" className={styles.section}>
          <h2 className={styles.sectionTitle} id="covenants-heading">
            Cam kết tài chính
          </h2>
          {data.covenants.length === 0 ? (
            <p className={styles.sectionLead}>Chưa khai báo cam kết nào.</p>
          ) : (
            <ul className={styles.list}>
              {data.covenants.map((covenant) => (
                <CovenantCard
                  api={api}
                  caseId={caseId}
                  covenant={covenant}
                  key={covenant.id}
                  onChanged={() => void refresh()}
                  tests={data.covenantTests.filter((test) => test.covenantId === covenant.id)}
                />
              ))}
            </ul>
          )}
          <CreateCovenantForm
            onReload={() => void refresh()}
            onSubmit={async (input) => {
              await api.createCovenant(caseId, input);
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

function ObservationCard({ observation }: { observation: Observation }) {
  return (
    <li className={styles.entry}>
      <div className={styles.entryHead}>
        <p className={styles.entryTitle}>{observation.observationType}</p>
        {observation.obligationId ? (
          <span className="status-chip status-chip--muted">
            Nghĩa vụ {shortId(observation.obligationId)}
          </span>
        ) : null}
      </div>
      <p className={styles.entryText}>{observation.body}</p>
      {/* Three DISTINCT timestamps, each labelled so they are never conflated. */}
      <p className={styles.entryMeta}>Hiệu lực: {formatDateTime(observation.effectiveAt)}</p>
      <p className={styles.entryMeta}>Quan sát: {formatDateTime(observation.observedAt)}</p>
      <p className={styles.entryMeta}>Ghi nhận: {formatDateTime(observation.recordedAt)}</p>
      {observation.evidenceRefs.length > 0 ? (
        <ul className={styles.refList}>
          {observation.evidenceRefs.map((ref) => (
            <li className={styles.ref} key={ref}>
              {ref}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function CovenantCard({
  caseId,
  api,
  covenant,
  tests,
  onChanged,
}: {
  caseId: string;
  api: MonitoringApi;
  covenant: Covenant;
  tests: CovenantTest[];
  onChanged: () => void;
}) {
  return (
    <li className={styles.entry}>
      <div className={styles.entryHead}>
        <p className={styles.entryTitle}>{covenant.name}</p>
        <span className="status-chip status-chip--muted">
          {covenant.metricKey}{" "}
          {labelOrUnsupported(COMPARISON_OPERATOR_LABELS, String(covenant.operator))}{" "}
          {covenant.thresholdValue}
        </span>
      </div>
      <p className={styles.entryMeta}>Phiên bản ngưỡng v{covenant.thresholdVersion}</p>
      {tests.length > 0 ? (
        <ul className={styles.items}>
          {tests.map((test) => (
            <li className={styles.item} key={test.id}>
              <p className={styles.entryMeta}>
                Kiểm tra {test.numerator} / {test.denominator} · so sánh {test.comparisonLhs}{" "}
                {labelOrUnsupported(COMPARISON_OPERATOR_SYMBOLS, String(test.operator))}{" "}
                {test.comparisonRhs}
              </p>
              <span
                className={`status-chip ${test.passed ? "status-chip--ok" : "status-chip--risk"}`}
              >
                {test.passed ? "Đạt" : "Không đạt (vi phạm cam kết)"}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <RunCovenantTestForm
        onReload={onChanged}
        onSubmit={async (input) => {
          await api.runCovenantTest(caseId, covenant.id, input);
          onChanged();
        }}
      />
    </li>
  );
}

function AlertCard({
  caseId,
  api,
  alert,
  onChanged,
}: {
  caseId: string;
  api: MonitoringApi;
  alert: Alert;
  onChanged: () => void;
}) {
  const targets = allowedAlertTransitions(String(alert.status));
  return (
    <li className={styles.entry}>
      <div className={styles.entryHead}>
        <p className={styles.entryTitle}>
          {labelOrUnsupported(ALERT_RULE_LABELS, String(alert.rule))}
        </p>
        <span className="status-chip status-chip--muted">
          {labelOrUnsupported(ALERT_STATUS_LABELS, String(alert.status))}
        </span>
      </div>
      <p className={styles.entryText}>{alert.detail}</p>
      <p className={styles.entryMeta}>{formatDateTime(alert.createdAt)}</p>
      {targets.length > 0 ? (
        <AlertDispositionForm
          onReload={onChanged}
          onSubmit={async (toStatus, rationale) => {
            await api.disposeAlert(caseId, alert.id, { toStatus, rationale });
            onChanged();
          }}
          targets={targets}
        />
      ) : (
        <p className={styles.entryMeta}>Trạng thái kết thúc: không còn bước xử lý.</p>
      )}
    </li>
  );
}

function AlertDispositionForm({
  targets,
  onSubmit,
  onReload,
}: {
  targets: readonly AlertStatus[];
  onSubmit: (toStatus: AlertStatus, rationale: string) => Promise<void>;
  onReload: () => void;
}) {
  const groupName = useId();
  const [target, setTarget] = useState<AlertStatus | "">("");
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
    if (!target) {
      setFieldError("Chọn hướng xử lý cảnh báo.");
      return;
    }
    const note = rationale.trim();
    if (note.length === 0) {
      setFieldError("Xử lý cảnh báo là quyết định có thẩm quyền: bắt buộc nhập lý do.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit(target, note);
      setTarget("");
      setRationale("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getMonitoringError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <div className={styles.field}>
        <span className={styles.fieldLabel}>
          Xử lý cảnh báo <span className={styles.required}>*</span>
        </span>
        <div aria-label="Hướng xử lý cảnh báo" className={styles.radioGroup} role="radiogroup">
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
              <span>{ALERT_TRANSITION_LABELS[option]}</span>
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
          {pending ? "Đang ghi xử lý…" : "Ghi nhận xử lý cảnh báo"}
        </button>
      </div>
    </form>
  );
}

function CreateObligationsForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: CreateObligationsInput) => Promise<void>;
  onReload: () => void;
}) {
  const formId = useId();
  const [frequency, setFrequency] = useState<ObligationFrequency>("MONTHLY");
  const [requirementText, setRequirementText] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [count, setCount] = useState("1");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const text = requirementText.trim();
    const parsedCount = Number(count);
    if (text.length === 0 || !fromDate || !Number.isInteger(parsedCount) || parsedCount < 1) {
      setFieldError("Nhập nội dung nghĩa vụ, ngày bắt đầu và số kỳ hợp lệ (≥ 1).");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({ frequency, requirementText: text, fromDate, count: parsedCount });
      setRequirementText("");
      setFromDate("");
      setCount("1");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getMonitoringError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <h3 className={styles.formHeading}>Tạo lịch nghĩa vụ giám sát</h3>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-req`}>
          Nội dung nghĩa vụ <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id={`${formId}-req`}
          maxLength={4000}
          onChange={(event) => {
            setRequirementText(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={requirementText}
        />
      </div>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-freq`}>
            Tần suất
          </label>
          <select
            className={styles.select}
            disabled={pending}
            id={`${formId}-freq`}
            onChange={(event) => setFrequency(event.target.value as ObligationFrequency)}
            value={frequency}
          >
            {OBLIGATION_FREQUENCIES.map((option) => (
              <option key={option} value={option}>
                {OBLIGATION_FREQUENCY_LABELS[option]}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-from`}>
            Ngày bắt đầu <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-from`}
            onChange={(event) => {
              setFromDate(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            type="date"
            value={fromDate}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-count`}>
            Số kỳ <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-count`}
            inputMode="numeric"
            max={120}
            min={1}
            onChange={(event) => {
              setCount(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            type="number"
            value={count}
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
          {pending ? "Đang tạo lịch nghĩa vụ…" : "Tạo lịch nghĩa vụ"}
        </button>
      </div>
    </form>
  );
}

function CreateObservationForm({
  obligations,
  onSubmit,
  onReload,
}: {
  obligations: Obligation[];
  onSubmit: (input: CreateObservationInput) => Promise<void>;
  onReload: () => void;
}) {
  const formId = useId();
  const [observationType, setObservationType] = useState("");
  const [body, setBody] = useState("");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [observedAt, setObservedAt] = useState("");
  const [obligationId, setObligationId] = useState("");
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
    const type = observationType.trim();
    const text = body.trim();
    if (type.length === 0 || text.length === 0 || !effectiveAt || !observedAt) {
      setFieldError("Nhập loại quan sát, nội dung, thời điểm hiệu lực và thời điểm quan sát.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        observationType: type,
        body: text,
        effectiveAt: new Date(effectiveAt).toISOString(),
        observedAt: new Date(observedAt).toISOString(),
        obligationId: obligationId || undefined,
        evidenceRefs: splitRefs(evidence),
      });
      setObservationType("");
      setBody("");
      setEffectiveAt("");
      setObservedAt("");
      setObligationId("");
      setEvidence("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getMonitoringError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <h3 className={styles.formHeading}>Ghi nhận quan sát</h3>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-type`}>
          Loại quan sát <span className={styles.required}>*</span>
        </label>
        <input
          className={styles.input}
          disabled={pending}
          id={`${formId}-type`}
          maxLength={200}
          onChange={(event) => {
            setObservationType(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={observationType}
        />
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-body`}>
          Nội dung <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id={`${formId}-body`}
          maxLength={8000}
          onChange={(event) => {
            setBody(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={body}
        />
      </div>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-effective`}>
            Thời điểm hiệu lực <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-effective`}
            onChange={(event) => {
              setEffectiveAt(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            type="datetime-local"
            value={effectiveAt}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-observed`}>
            Thời điểm quan sát <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-observed`}
            onChange={(event) => {
              setObservedAt(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            type="datetime-local"
            value={observedAt}
          />
        </div>
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-obligation`}>
          Gắn với nghĩa vụ (không bắt buộc)
        </label>
        <select
          className={styles.select}
          disabled={pending}
          id={`${formId}-obligation`}
          onChange={(event) => setObligationId(event.target.value)}
          value={obligationId}
        >
          <option value="">— Không gắn —</option>
          {obligations.map((obligation) => (
            <option key={obligation.id} value={obligation.id}>
              Kỳ {obligation.sequence} · hạn {formatDate(obligation.dueDate)}
            </option>
          ))}
        </select>
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
          {pending ? "Đang ghi quan sát…" : "Ghi nhận quan sát"}
        </button>
      </div>
    </form>
  );
}

function CreateCovenantForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: CreateCovenantInput) => Promise<void>;
  onReload: () => void;
}) {
  const formId = useId();
  const [name, setName] = useState("");
  const [metricKey, setMetricKey] = useState("");
  const [operator, setOperator] = useState<ComparisonOperator>("GTE");
  const [thresholdValue, setThresholdValue] = useState("");
  const [thresholdVersion, setThresholdVersion] = useState("1");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const trimmedName = name.trim();
    const key = metricKey.trim();
    const threshold = thresholdValue.trim();
    const parsedVersion = Number(thresholdVersion);
    if (
      trimmedName.length === 0 ||
      key.length === 0 ||
      threshold.length === 0 ||
      !Number.isInteger(parsedVersion) ||
      parsedVersion < 1
    ) {
      setFieldError("Nhập tên, khoá chỉ số, ngưỡng và phiên bản ngưỡng hợp lệ (≥ 1).");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({
        name: trimmedName,
        metricKey: key,
        operator,
        thresholdValue: threshold,
        thresholdVersion: parsedVersion,
      });
      setName("");
      setMetricKey("");
      setThresholdValue("");
      setThresholdVersion("1");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getMonitoringError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <h3 className={styles.formHeading}>Khai báo cam kết</h3>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={`${formId}-name`}>
          Tên cam kết <span className={styles.required}>*</span>
        </label>
        <input
          className={styles.input}
          disabled={pending}
          id={`${formId}-name`}
          maxLength={400}
          onChange={(event) => {
            setName(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={name}
        />
      </div>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-metric`}>
            Khoá chỉ số <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-metric`}
            maxLength={200}
            onChange={(event) => {
              setMetricKey(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            value={metricKey}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-op`}>
            Toán tử
          </label>
          <select
            className={styles.select}
            disabled={pending}
            id={`${formId}-op`}
            onChange={(event) => setOperator(event.target.value as ComparisonOperator)}
            value={operator}
          >
            {COMPARISON_OPERATORS.map((option) => (
              <option key={option} value={option}>
                {COMPARISON_OPERATOR_LABELS[option]}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-threshold`}>
            Ngưỡng <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-threshold`}
            inputMode="decimal"
            onChange={(event) => {
              setThresholdValue(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            value={thresholdValue}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-version`}>
            Phiên bản ngưỡng <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-version`}
            inputMode="numeric"
            min={1}
            onChange={(event) => {
              setThresholdVersion(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            type="number"
            value={thresholdVersion}
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
          {pending ? "Đang khai báo cam kết…" : "Khai báo cam kết"}
        </button>
      </div>
    </form>
  );
}

function RunCovenantTestForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: RunCovenantTestInput) => Promise<void>;
  onReload: () => void;
}) {
  const formId = useId();
  const [numerator, setNumerator] = useState("");
  const [denominator, setDenominator] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const num = numerator.trim();
    if (num.length === 0) {
      setFieldError("Nhập tử số để kiểm tra cam kết.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({ numerator: num, denominator: denominator.trim() || undefined });
      setNumerator("");
      setDenominator("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getMonitoringError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <h4 className={styles.formHeading}>Kiểm tra cam kết</h4>
      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-num`}>
            Tử số <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-num`}
            inputMode="decimal"
            onChange={(event) => {
              setNumerator(event.target.value);
              if (fieldError) setFieldError(null);
            }}
            value={numerator}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={`${formId}-den`}>
            Mẫu số (không bắt buộc, mặc định 1)
          </label>
          <input
            className={styles.input}
            disabled={pending}
            id={`${formId}-den`}
            inputMode="decimal"
            onChange={(event) => setDenominator(event.target.value)}
            value={denominator}
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
          {pending ? "Đang kiểm tra…" : "Kiểm tra cam kết"}
        </button>
      </div>
    </form>
  );
}
