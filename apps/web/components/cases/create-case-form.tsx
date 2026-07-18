"use client";

import Link from "next/link";
import React, { type FormEvent, useRef, useState } from "react";

import { creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type { CreditCaseDto } from "../../lib/api/contracts";
import { EvidenceChip, shortReference } from "./evidence-chip";
import styles from "./create-case-form.module.css";

interface CreateCaseFormProps {
  api?: Pick<typeof creditOpsApi, "createCase">;
  canCreateCase: boolean;
}

interface FormErrors {
  requestedAmount?: string;
  purpose?: string;
}

export function CreateCaseForm({
  api = creditOpsApi,
  canCreateCase,
}: CreateCaseFormProps) {
  const [requestedAmount, setRequestedAmount] = useState("");
  const [purpose, setPurpose] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [createdCase, setCreatedCase] = useState<CreditCaseDto | null>(null);
  const [validationSummary, setValidationSummary] = useState<string | null>(null);
  const amountRef = useRef<HTMLInputElement>(null);
  const purposeRef = useRef<HTMLTextAreaElement>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors: FormErrors = {};
    const amount = requestedAmount.trim();
    const financingPurpose = purpose.trim();

    if (!amount) nextErrors.requestedAmount = "Nhập số tiền đề nghị.";
    else if (!/^\d+$/.test(amount) || BigInt(amount) <= BigInt(0)) {
      nextErrors.requestedAmount = "Số tiền đề nghị phải là số nguyên dương.";
    }
    if (!financingPurpose) nextErrors.purpose = "Nhập mục đích vay vốn.";
    setErrors(nextErrors);
    const errorCount = Object.keys(nextErrors).length;
    if (errorCount > 0) {
      setValidationSummary(`Có ${errorCount} trường cần kiểm tra.`);
      if (nextErrors.requestedAmount) amountRef.current?.focus();
      else purposeRef.current?.focus();
      return;
    }
    setValidationSummary(null);

    setSubmitting(true);
    setRequestError(null);
    try {
      setCreatedCase(
        await api.createCase({ requestedAmount: amount, purpose: financingPurpose }),
      );
    } catch (error) {
      setRequestError(getVietnameseApiError(error));
    } finally {
      setSubmitting(false);
    }
  }

  if (!canCreateCase) {
    return (
      <div className={styles.panel} role="alert">
        <h2 className={styles.panelTitle}>Không thể tạo hồ sơ minh họa</h2>
        <p>Bạn không có quyền tạo hồ sơ minh họa.</p>
      </div>
    );
  }

  if (createdCase) {
    return (
      <div className={`${styles.panel} ${styles.panelReceipt}`} role="status">
        <EvidenceChip
          label={`Hồ sơ · phiên bản ${createdCase.version}`}
          reference={shortReference(createdCase.id)}
          title={`Mã hồ sơ: ${createdCase.id}`}
        />
        <h2 className={styles.panelTitle}>Đã tạo hồ sơ</h2>
        <p>Thông tin tổng hợp dùng cho trình diễn đã được ghi nhận. Tài liệu chỉ được đăng ký sau khi kho lưu trữ xác minh.</p>
        <Link
          className="button button-primary"
          href={`/ho-so/${encodeURIComponent(createdCase.id)}/tiep-nhan`}
        >
          Mở hồ sơ vừa tạo
        </Link>
      </div>
    );
  }

  return (
    <form className={styles.form} noValidate onSubmit={submit}>
      {validationSummary ? (
        <p aria-live="assertive" className={styles.alert} role="alert">
          {validationSummary}
        </p>
      ) : null}
      <div className={styles.fieldGroup}>
        <label htmlFor="requested-amount">Số tiền đề nghị</label>
        <div className={styles.inputSuffix}>
          <input
            aria-describedby={errors.requestedAmount ? "requested-amount-error" : "requested-amount-help"}
            aria-invalid={Boolean(errors.requestedAmount)}
            autoComplete="off"
            id="requested-amount"
            inputMode="numeric"
            onChange={(event) => setRequestedAmount(event.target.value)}
            placeholder="Ví dụ: 5000000000"
            ref={amountRef}
            value={requestedAmount}
          />
          <span>VND</span>
        </div>
        <p className={styles.fieldHelp} id="requested-amount-help">
          Nhập số nguyên, không dùng dấu phân cách.
        </p>
        {errors.requestedAmount ? (
          <p className={styles.fieldError} id="requested-amount-error">
            {errors.requestedAmount}
          </p>
        ) : null}
      </div>

      <div className={styles.fieldGroup}>
        <label htmlFor="financing-purpose">Mục đích vay vốn</label>
        <textarea
          aria-describedby={errors.purpose ? "purpose-error" : "purpose-help"}
          aria-invalid={Boolean(errors.purpose)}
          id="financing-purpose"
          maxLength={500}
          onChange={(event) => setPurpose(event.target.value)}
          placeholder="Mô tả nhu cầu vốn lưu động do cán bộ ghi nhận"
          ref={purposeRef}
          rows={5}
          value={purpose}
        />
        <p className={styles.fieldHelp} id="purpose-help">
          Chỉ nhập dữ liệu tổng hợp dùng cho trình diễn; không dùng dữ liệu khách hàng thật.
        </p>
        {errors.purpose ? (
          <p className={styles.fieldError} id="purpose-error">
            {errors.purpose}
          </p>
        ) : null}
      </div>

      {requestError ? <p className={styles.alert} role="alert">{requestError}</p> : null}
      <div className={styles.actions}>
        <button className="button button-primary" disabled={submitting} type="submit">
          {submitting ? "Đang tạo hồ sơ…" : "Tạo hồ sơ"}
        </button>
        <Link className="button button-quiet" href="/ho-so">
          Hủy
        </Link>
      </div>
    </form>
  );
}
