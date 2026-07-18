"use client";

import React, { type ChangeEvent, useRef, useState } from "react";

import { creditOpsApi, getVietnameseApiError } from "../../lib/api/client";
import type { UploadIntentDto } from "../../lib/api/contracts";
import {
  directUploadTransport,
  type DirectUploadTransport,
  type UploadItem,
  uploadFromIntent,
} from "../../lib/upload/upload-machine";
import { DirectStorageError } from "../../lib/upload/signed-upload";
import { UploadProgress } from "./upload-progress";
import styles from "./upload-zone.module.css";

const ACCEPTED_CONTENT = new Map([
  [".pdf", "application/pdf"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  [".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
]);

interface UploadZoneProps {
  caseId: string;
  canUpload: boolean;
  api?: Pick<typeof creditOpsApi, "createUploadIntent" | "completeUploadIntent">;
  transport?: DirectUploadTransport;
}

export function UploadZone({
  caseId,
  canUpload,
  api = creditOpsApi,
  transport = directUploadTransport,
}: UploadZoneProps) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  const sessions = useRef(new Map<string, UploadSession>());
  const sequence = useRef(0);

  function update(id: string, patch: Partial<UploadItem>) {
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    );
  }

  function selectFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    const nextItems = files.map((file) => {
      const id = `${Date.now()}-${sequence.current++}`;
      const validationError = validateFile(file);
      return {
        id,
        file,
        status: validationError ? "FAILED" : "REQUESTING_INTENT",
        progress: 0,
        error: validationError,
        duplicateOfDocumentId: null,
        taskStatus: null,
      } satisfies UploadItem;
    });
    setItems((current) => [...current, ...nextItems]);
    for (const item of nextItems) {
      if (!item.error) void createIntentAndUpload(item.id, item.file);
    }
    event.target.value = "";
  }

  async function createIntentAndUpload(id: string, file: File) {
    sessions.current.delete(id);
    const controller = new AbortController();
    controllers.current.set(id, controller);
    update(id, {
      status: "REQUESTING_INTENT",
      progress: 0,
      error: null,
      duplicateOfDocumentId: null,
      taskStatus: null,
    });

    try {
      const contentType = contentTypeFor(file);
      const intent = await api.createUploadIntent(caseId, {
        fileName: file.name,
        contentType,
        sizeBytes: file.size,
      });
      if (!validFutureDate(intent.expiresAt)) {
        throw new ExpiredUploadError();
      }
      if (controller.signal.aborted) throw new DOMException("Cancelled", "AbortError");
      const session: UploadSession = {
        intent,
        idempotencyKey: randomIdempotencyKey(),
        phase: "UPLOAD",
      };
      sessions.current.set(id, session);
      await performDirectUpload(id, file, session, controller);
    } catch (error) {
      handleFailure(id, error, controller);
    } finally {
      deleteController(id, controller);
    }
  }

  async function performDirectUpload(
    id: string,
    file: File,
    session: UploadSession,
    controller: AbortController,
  ) {
    update(id, {
      status: "UPLOADING",
      progress: 0,
      error: null,
      duplicateOfDocumentId: null,
      taskStatus: null,
    });

    try {
      await uploadFromIntent(transport, session.intent, file, {
        signal: controller.signal,
        onProgress: (progress) => update(id, { progress }),
        resumeUrl: session.resumableUploadUrl,
        onResumeUrl: (resumeUrl) => {
          if (sessions.current.get(id) === session) {
            session.resumableUploadUrl = resumeUrl;
          }
        },
      });
      if (controller.signal.aborted) throw new DOMException("Cancelled", "AbortError");
    } catch (error) {
      if (
        session.intent.mode === "RESUMABLE" &&
        isTerminalResumableSessionError(error)
      ) {
        sessions.current.delete(id);
      }
      handleFailure(id, error, controller);
      return;
    } finally {
      deleteController(id, controller);
    }

    session.phase = "COMPLETION";
    await completeExistingIntent(id, session);
  }

  async function completeExistingIntent(id: string, session: UploadSession) {
    update(id, { status: "VERIFYING", progress: 100, error: null });
    try {
      const result = await api.completeUploadIntent(
        session.intent.intentId,
        session.idempotencyKey,
      );
      if (result.outcome === "DUPLICATE") {
        update(id, {
          status: "DUPLICATE",
          duplicateOfDocumentId: result.duplicateOfDocumentId,
          taskStatus: null,
        });
      } else if (result.outcome === "REGISTERED") {
        update(id, {
          status: "REGISTERED",
          duplicateOfDocumentId: null,
          taskStatus: result.task.status,
        });
      } else {
        throw new Error("UPLOAD_COMPLETION_INVALID_OUTCOME");
      }
      sessions.current.delete(id);
    } catch (error) {
      update(id, { status: "FAILED", error: getVietnameseApiError(error) });
    }
  }

  function handleFailure(
    id: string,
    error: unknown,
    controller: AbortController,
  ) {
    if (controller.signal.aborted || isAbortError(error)) {
      update(id, { status: "CANCELLED", error: null });
    } else if (error instanceof ExpiredUploadError) {
      update(id, {
        status: "FAILED",
        error: "Phiên tải lên đã hết hạn. Vui lòng thử lại.",
      });
    } else {
      update(id, { status: "FAILED", error: getVietnameseApiError(error) });
    }
  }

  function deleteController(id: string, controller: AbortController) {
    if (controllers.current.get(id) === controller) {
      controllers.current.delete(id);
    }
  }

  function cancel(id: string) {
    controllers.current.get(id)?.abort();
    update(id, { status: "CANCELLED", error: null });
  }

  function retry(id: string) {
    const item = items.find((candidate) => candidate.id === id);
    if (!item) return;

    const session = sessions.current.get(id);
    if (session?.phase === "COMPLETION") {
      void completeExistingIntent(id, session);
      return;
    }
    if (
      session?.phase === "UPLOAD" &&
      session.intent.mode === "RESUMABLE" &&
      session.resumableUploadUrl &&
      validFutureDate(session.intent.expiresAt)
    ) {
      const controller = new AbortController();
      controllers.current.set(id, controller);
      void performDirectUpload(id, item.file, session, controller);
      return;
    }
    void createIntentAndUpload(item.id, item.file);
  }

  if (!canUpload) {
    return (
      <div className="state-panel" role="alert">
        <h2>Không thể tải tài liệu</h2>
        <p>Bạn không có quyền tải tài liệu cho hồ sơ này.</p>
      </div>
    );
  }

  return (
    <section aria-labelledby="upload-title" className={styles.panel}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Tải trực tiếp có kiểm soát</p>
          <h2 className={styles.title} id="upload-title">Thêm tài liệu vào hồ sơ</h2>
        </div>
        <span className={styles.privateBadge}>Kho riêng tư</span>
      </div>
      <p className={styles.copy}>
        Bản trình diễn chỉ dùng tài liệu tổng hợp. Trình duyệt gửi tệp thẳng tới kho tài liệu bằng quyền tải lên ngắn hạn; backend phải xác minh đối tượng đã lưu trước khi đăng ký.
      </p>
      <div className={styles.dropzone}>
        <input
          accept={Array.from(ACCEPTED_CONTENT.keys()).join(",")}
          className={styles.input}
          id="document-files"
          multiple
          onChange={selectFiles}
          type="file"
        />
        <label className="button button-primary" htmlFor="document-files">
          Chọn tài liệu
        </label>
        <span className={styles.hint}>PDF, PNG, JPEG, DOCX hoặc XLSX</span>
      </div>
      {items.length > 0 ? (
        <ul aria-label="Tiến độ tải tài liệu" className={styles.list}>
          {items.map((item) => (
            <UploadProgress
              item={item}
              key={item.id}
              onCancel={cancel}
              onRetry={retry}
            />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function validateFile(file: File): string | null {
  const extension = extensionOf(file.name);
  const expectedType = ACCEPTED_CONTENT.get(extension);
  if (!expectedType || (file.type && file.type !== expectedType)) {
    return "Định dạng tài liệu chưa được hỗ trợ.";
  }
  return null;
}

function contentTypeFor(file: File): string {
  return file.type || ACCEPTED_CONTENT.get(extensionOf(file.name)) || "application/octet-stream";
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index < 0 ? "" : name.slice(index).toLowerCase();
}

function validFutureDate(value: string): boolean {
  const expiresAt = new Date(value).getTime();
  return Number.isFinite(expiresAt) && expiresAt > Date.now();
}

function randomIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function isTerminalResumableSessionError(error: unknown): boolean {
  return (
    error instanceof DirectStorageError &&
    ([401, 403, 404, 410].includes(error.status) ||
      ["TUS_OFFSET_INVALID", "TUS_LOCATION_INVALID"].includes(error.code))
  );
}

class ExpiredUploadError extends Error {}

interface UploadSession {
  intent: UploadIntentDto;
  idempotencyKey: string;
  phase: "UPLOAD" | "COMPLETION";
  resumableUploadUrl?: string;
}
