"use client";

import React, { useCallback, useEffect, useState } from "react";

import { ApiClientError } from "../../lib/api/client";
import {
  ContractPackagesApiClient,
  contractPackagesApi,
  formatDateTime,
  getContractError,
  isContractPackageNotAvailable,
  isMaterialChange,
  labelOrUnsupported,
  MOCK_CONTRACT_LABEL_VI,
  PACKAGE_STATE_LABELS,
  SIGNATURE_KIND_LABELS,
  shortId,
  type ContractPackageView,
} from "../../lib/api/contract-packages";
import { RecordActionForm } from "../gates/record-action-form";
import { CaseNav } from "../shell/case-nav";
import styles from "../gates/gates.module.css";

type ContractApi = Pick<
  ContractPackagesApiClient,
  "getView" | "createPackage" | "addRedline" | "approve" | "confirmSignatureAuthority" | "sign"
>;

const MAX_TEXT = 4000;
const MAX_CONTENT = 200_000;

// Stage-8 contract-package workspace. The contract text is a deterministic
// render (mock, no legal effect — the disclaimer is always shown). Redlines are
// versioned. The three signing gates are each their own section, pinned to the
// exact package version. A material-change 409 fences the package into a
// distinct blocking state that routes back to the decision stage. Signing
// records MOCK signature evidence only. No polling; refresh is manual.
export function ContractWorkspace({
  caseId,
  api = contractPackagesApi,
}: {
  caseId: string;
  api?: ContractApi;
}) {
  const [view, setView] = useState<ContractPackageView | null>(null);
  const [absent, setAbsent] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [materialChange, setMaterialChange] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setUnauthorized(false);
    setAbsent(false);
    setRefreshError(null);
    try {
      setView(await api.getView(caseId));
    } catch (requestError) {
      if (isContractPackageNotAvailable(requestError)) {
        setView(null);
        setAbsent(true);
      } else if (requestError instanceof ApiClientError && requestError.status === 403) {
        setUnauthorized(true);
      } else {
        setError(getContractError(requestError));
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
      const next = await api.getView(caseId);
      setView(next);
      setAbsent(false);
    } catch (requestError) {
      if (isContractPackageNotAvailable(requestError)) {
        setView(null);
        setAbsent(true);
      } else {
        setRefreshError(getContractError(requestError));
      }
    }
  }, [api, caseId]);

  const createPackage = useCallback(async () => {
    setCreating(true);
    setCreateError(null);
    try {
      await api.createPackage(caseId);
      await refresh();
    } catch (requestError) {
      setCreateError(getContractError(requestError));
    } finally {
      setCreating(false);
    }
  }, [api, caseId, refresh]);

  // Wraps a gate write so a material-change 409 becomes the distinct fenced
  // state rather than a routine inline error; other errors propagate to the
  // form's own 409-keep-draft handling.
  const runGateWrite = useCallback(
    async (write: () => Promise<unknown>) => {
      try {
        await write();
        await refresh();
      } catch (requestError) {
        if (isMaterialChange(requestError)) {
          setMaterialChange(true);
          await refresh();
          return;
        }
        throw requestError;
      }
    },
    [refresh],
  );

  if (loading) {
    return (
      <div aria-busy="true" aria-label="Đang tải hồ sơ hợp đồng" className="case-skeleton" role="status">
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (unauthorized) {
    return (
      <>
        <CaseNav caseId={caseId} current="hop-dong" />
        <div className="state-panel" role="alert">
          <p>Bạn không có vai trò tham gia hồ sơ để xem hồ sơ hợp đồng.</p>
        </div>
      </>
    );
  }

  if (error) {
    return (
      <>
        <CaseNav caseId={caseId} current="hop-dong" />
        <div className="state-panel" role="alert">
          <p>{error}</p>
          <button className="button button-secondary" onClick={() => void load()} type="button">
            Thử tải lại
          </button>
        </div>
      </>
    );
  }

  const pkg = view?.package ?? null;
  const fenced = materialChange || pkg?.state === "MATERIAL_CHANGE_DETECTED";
  const signed = view?.signatureEvidence != null;

  return (
    <>
      <CaseNav caseId={caseId} current="hop-dong" />
      <div className="page-heading">
        <p className="eyebrow">Giai đoạn 8 · Hồ sơ hợp đồng</p>
        <h1>Hồ sơ hợp đồng</h1>
      </div>

      <p className={styles.disclaimer} role="note">
        {MOCK_CONTRACT_LABEL_VI}
      </p>

      <div className={styles.workspace}>
        {absent || !pkg || !view ? (
          <div className="empty-state">
            <p className="empty-state-title">Chưa có hồ sơ hợp đồng</p>
            <p className="empty-state-hint">
              Gói hợp đồng được kết xuất tất định từ quyết định phê duyệt và bộ điều khoản đã
              chốt. Hệ thống không tự tạo khi mở trang.
            </p>
            <div className="empty-state-action">
              <button
                aria-busy={creating}
                className="button button-primary"
                disabled={creating}
                onClick={() => void createPackage()}
                type="button"
              >
                {creating ? "Đang lập hồ sơ…" : "Lập hồ sơ hợp đồng từ điều khoản đã duyệt"}
              </button>
            </div>
            {createError ? (
              <div className="state-panel" role="alert">
                <p>{createError}</p>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <div className={styles.gateRow}>
              <span
                className={`status-chip ${
                  fenced
                    ? "status-chip--risk"
                    : signed
                      ? "status-chip--ok"
                      : "status-chip--muted"
                }`}
              >
                Trạng thái: {labelOrUnsupported(PACKAGE_STATE_LABELS, String(pkg.state))}
              </span>
              <span className={styles.meta}>
                Phiên bản gói v{pkg.packageVersion} · Hồ sơ v{pkg.caseVersion} · Mã gói{" "}
                {shortId(pkg.id)}
              </span>
            </div>

            <section aria-labelledby="content-heading" className={styles.section}>
              <h2 className={styles.sectionTitle} id="content-heading">
                Nội dung gói hợp đồng
              </h2>
              <p className={styles.entryMeta}>
                Quyết định nguồn {shortId(pkg.decisionId)} · Người lập {shortId(pkg.createdBy)} ·{" "}
                {formatDateTime(pkg.createdAt)}
              </p>
              <pre className={styles.document}>{pkg.content}</pre>
              <p className={styles.hashLine}>Content sha256: {pkg.contentHash}</p>
              <p className={styles.hashLine}>Term snapshot: {pkg.termSnapshotHash}</p>
            </section>

            {fenced ? (
              <div className={styles.blockedBanner} role="alert">
                <p className={styles.blockedTitle}>Phát hiện thay đổi trọng yếu</p>
                <p>
                  Điều khoản hợp đồng không còn khớp quyết định tín dụng hiện tại. Gói hợp đồng bị
                  khóa; hồ sơ phải quay lại giai đoạn quyết định (stage 6) để tạo quyết định mới
                  trước khi tiếp tục ký kết.
                </p>
              </div>
            ) : null}

            {view.redlines.length > 0 ? (
              <section aria-labelledby="redlines-heading" className={styles.section}>
                <h2 className={styles.sectionTitle} id="redlines-heading">
                  Lịch sử chỉnh sửa pháp lý
                </h2>
                <ul className={styles.list}>
                  {view.redlines.map((redline) => (
                    <li className={styles.entry} key={redline.id}>
                      <div className={styles.entryHead}>
                        <p className={styles.entryTitle}>Bản chỉnh sửa v{redline.redlineVersion}</p>
                        <span className={styles.meta}>
                          {shortId(redline.createdBy)} · {formatDateTime(redline.createdAt)}
                        </span>
                      </div>
                      <p className={styles.entryText}>{redline.changeNote}</p>
                      <p className={styles.hashLine}>
                        Nội dung sha256: {redline.changedContentHash}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {signed && view.signatureEvidence ? (
              <section aria-labelledby="signed-heading" className={styles.section}>
                <h2 className={styles.sectionTitle} id="signed-heading">
                  {labelOrUnsupported(SIGNATURE_KIND_LABELS, String(view.signatureEvidence.kind))}
                </h2>
                <p className={styles.sectionLead}>
                  Đây là bằng chứng ký mô phỏng; không có ký điện tử thật và không thực thi hợp
                  đồng.
                </p>
                <p className={styles.entryText}>
                  Người ký: {view.signatureEvidence.signerNames.join(", ") || "—"}
                </p>
                {view.signatureEvidence.evidenceNote ? (
                  <p className={styles.entryText}>{view.signatureEvidence.evidenceNote}</p>
                ) : null}
                <p className={styles.entryMeta}>
                  Người ghi nhận {shortId(view.signatureEvidence.recordedBy)} ·{" "}
                  {formatDateTime(view.signatureEvidence.createdAt)}
                </p>
              </section>
            ) : null}

            {!fenced && !signed ? (
              <>
                <section aria-labelledby="redline-heading" className={styles.section}>
                  <h2 className={styles.sectionTitle} id="redline-heading">
                    Ghi nhận bản chỉnh sửa pháp lý
                  </h2>
                  <p className={styles.sectionLead}>
                    Mỗi bản chỉnh sửa tạo một phiên bản gói mới (không sửa đè). Rà soát pháp lý ghi
                    nội dung thay thế và ghi chú thay đổi.
                  </p>
                  <RedlineForm
                    onReload={() => void refresh()}
                    onSubmit={async (input) => {
                      await api.addRedline(caseId, input);
                      await refresh();
                    }}
                  />
                </section>

                <section aria-labelledby="approve-heading" className={styles.section}>
                  <h2 className={styles.sectionTitle} id="approve-heading">
                    Duyệt nội dung gói hợp đồng
                  </h2>
                  <p className={styles.pinned}>
                    Áp dụng cho gói v{pkg.packageVersion} · {pkg.id}
                  </p>
                  <RecordActionForm
                    formatError={getContractError}
                    hint="Kiểm soát tác nghiệp duyệt gói và thỏa mãn cổng HG_CONTRACT_PACKAGE_APPROVED. Máy chủ tự kiểm tra lại thay đổi trọng yếu trước khi duyệt."
                    onReload={() => void refresh()}
                    onSubmit={(rationale) =>
                      runGateWrite(() => api.approve(caseId, { rationale }))
                    }
                    rationaleLabel="Lý do duyệt gói hợp đồng"
                    submitLabel="Duyệt nội dung gói hợp đồng"
                  />
                </section>

                <section aria-labelledby="authority-heading" className={styles.section}>
                  <h2 className={styles.sectionTitle} id="authority-heading">
                    Xác nhận thẩm quyền ký kết
                  </h2>
                  <p className={styles.sectionLead}>
                    Cần duyệt gói hợp đồng trước; nếu chưa, máy chủ trả xung đột thứ tự cổng. Thỏa
                    mãn cổng HG_SIGNATURE_AUTHORITY_CONFIRMED.
                  </p>
                  <RecordActionForm
                    formatError={getContractError}
                    hint="Xác nhận người ký có thẩm quyền theo pháp luật và quy định nội bộ."
                    onReload={() => void refresh()}
                    onSubmit={(rationale) =>
                      runGateWrite(() =>
                        api.confirmSignatureAuthority(caseId, { rationale }),
                      )
                    }
                    rationaleLabel="Lý do xác nhận thẩm quyền ký"
                    submitLabel="Xác nhận thẩm quyền ký kết"
                  />
                </section>

                <section aria-labelledby="sign-heading" className={styles.section}>
                  <h2 className={styles.sectionTitle} id="sign-heading">
                    Ghi nhận chữ ký mô phỏng
                  </h2>
                  <p className={styles.sectionLead}>
                    Nhãn: {SIGNATURE_KIND_LABELS.MOCK_SIGNATURE}. Cần cả hai cổng trước đã đạt.
                    Đây không phải ký điện tử thật và không thực thi hợp đồng.
                  </p>
                  <SignForm
                    onReload={() => void refresh()}
                    onSubmit={(input) => runGateWrite(() => api.sign(caseId, input))}
                  />
                </section>
              </>
            ) : null}
          </>
        )}

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

// Legal redline: change note + full replacement content. Keeps the officer's
// draft on a 409 and offers a reload.
function RedlineForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: { changeNote: string; changedContent: string }) => Promise<void>;
  onReload: () => void;
}) {
  const [changeNote, setChangeNote] = useState("");
  const [changedContent, setChangedContent] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const note = changeNote.trim();
    const content = changedContent.trim();
    if (note.length === 0 || content.length === 0) {
      setFieldError("Nhập cả ghi chú thay đổi và nội dung thay thế; đây là trường bắt buộc.");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      await onSubmit({ changeNote: note, changedContent: content });
      setChangeNote("");
      setChangedContent("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getContractError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="redline-note">
          Ghi chú thay đổi <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id="redline-note"
          maxLength={MAX_TEXT}
          onChange={(event) => {
            setChangeNote(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={changeNote}
        />
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="redline-content">
          Nội dung thay thế <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id="redline-content"
          maxLength={MAX_CONTENT}
          onChange={(event) => {
            setChangedContent(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={changedContent}
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
          {pending ? "Đang ghi bản chỉnh sửa…" : "Ghi nhận bản chỉnh sửa pháp lý"}
        </button>
      </div>
    </form>
  );
}

// Mock signing: one signer name per line + optional evidence note.
function SignForm({
  onSubmit,
  onReload,
}: {
  onSubmit: (input: { signerNames: string[]; evidenceNote?: string }) => Promise<void>;
  onReload: () => void;
}) {
  const [signers, setSigners] = useState("");
  const [note, setNote] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [staleReload, setStaleReload] = useState(false);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (pending) return;
    setSubmitError(null);
    setStaleReload(false);
    const signerNames = signers
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    if (signerNames.length === 0) {
      setFieldError("Nhập ít nhất một người ký (mỗi dòng một tên).");
      return;
    }
    setFieldError(null);
    setPending(true);
    try {
      const trimmedNote = note.trim();
      await onSubmit({ signerNames, evidenceNote: trimmedNote || undefined });
      setSigners("");
      setNote("");
    } catch (requestError) {
      if (requestError instanceof ApiClientError && requestError.status === 409) {
        setStaleReload(true);
      }
      setSubmitError(getContractError(requestError));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className={styles.form} noValidate onSubmit={handleSubmit}>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="sign-signers">
          Người ký (mỗi dòng một tên) <span className={styles.required}>*</span>
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id="sign-signers"
          onChange={(event) => {
            setSigners(event.target.value);
            if (fieldError) setFieldError(null);
          }}
          value={signers}
        />
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="sign-note">
          Ghi chú bằng chứng (không bắt buộc)
        </label>
        <textarea
          className={styles.textarea}
          disabled={pending}
          id="sign-note"
          maxLength={MAX_TEXT}
          onChange={(event) => setNote(event.target.value)}
          value={note}
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
          {pending ? "Đang ghi chữ ký mô phỏng…" : "Ghi nhận chữ ký mô phỏng"}
        </button>
      </div>
    </form>
  );
}
