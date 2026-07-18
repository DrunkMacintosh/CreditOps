"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  getVietnameseApiError,
  orchestrationApi,
  type OrchestrationApi,
  type OrchestrationStatusDto,
  type TaskDetailDto,
} from "../../lib/api/orchestration";
import { CaseNav } from "../shell/case-nav";
import { PlannerQueue } from "./planner-queue";
import { StageRail } from "./stage-rail";
import { isReadinessInProgress, isTaskInFlight } from "./labels";
import styles from "./orchestration.module.css";

const POLL_INTERVAL_MS = 4000;
// Safety cap so a stuck in-flight state never polls forever; the officer can
// reload to resume observing.
const MAX_POLL_CYCLES = 20;

export function OrchestrationConsole({
  api = orchestrationApi,
  caseId,
}: {
  api?: OrchestrationApi;
  caseId: string;
}) {
  const [status, setStatus] = useState<OrchestrationStatusDto | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [advancing, setAdvancing] = useState(false);
  const [advanceError, setAdvanceError] = useState<string | null>(null);
  const [liveMessage, setLiveMessage] = useState("");
  const [taskDetails, setTaskDetails] = useState<Record<string, TaskDetailDto>>({});

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTokenRef = useRef(0);
  // The task-detail endpoint requires a role a case participant may not hold;
  // once it refuses we stop asking to avoid repeated 403 noise.
  const taskFetchDisabledRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    pollTokenRef.current += 1;
  }, []);

  const enrichTasks = useCallback(
    async (current: OrchestrationStatusDto) => {
      if (taskFetchDisabledRef.current) return;
      const currentVersionTasks = current.tasks.filter(
        (task) => task.caseVersion === current.caseVersion,
      );
      await Promise.all(
        currentVersionTasks.map(async (task) => {
          try {
            const detail = await api.getTask(task.taskId);
            if (mountedRef.current) {
              setTaskDetails((prev) => ({ ...prev, [task.taskId]: detail }));
            }
          } catch (error) {
            // A permission/absence refusal will not change within the session;
            // disable further enrichment rather than retry it every poll.
            if (
              error instanceof ApiClientError &&
              (error.status === 403 || error.status === 404)
            ) {
              taskFetchDisabledRef.current = true;
            }
          }
        }),
      );
    },
    [api],
  );

  const refreshSilently = useCallback(async (): Promise<OrchestrationStatusDto | null> => {
    try {
      const next = await api.getOrchestration(caseId);
      if (!mountedRef.current) return null;
      setStatus(next);
      setRefreshError(null);
      void enrichTasks(next);
      return next;
    } catch (error) {
      if (mountedRef.current) setRefreshError(getVietnameseApiError(error));
      return null;
    }
  }, [api, caseId, enrichTasks]);

  const runPollCycle = useCallback(
    async (token: number, remaining: number) => {
      if (!mountedRef.current || token !== pollTokenRef.current) return;
      const next = await refreshSilently();
      if (!mountedRef.current || token !== pollTokenRef.current) return;
      const stillWorking = next !== null && isInFlight(next);
      if (stillWorking && remaining > 0) {
        pollTimerRef.current = setTimeout(() => {
          void runPollCycle(token, remaining - 1);
        }, POLL_INTERVAL_MS);
        return;
      }
      setAdvancing(false);
      if (stillWorking) {
        setLiveMessage("Quy trình vẫn đang xử lý. Tải lại trang để cập nhật thêm.");
      } else if (next !== null) {
        setLiveMessage(settledSummary(next));
      }
    },
    [refreshSilently],
  );

  const beginPolling = useCallback(() => {
    stopPolling();
    const token = pollTokenRef.current;
    pollTimerRef.current = setTimeout(() => {
      void runPollCycle(token, MAX_POLL_CYCLES - 1);
    }, POLL_INTERVAL_MS);
  }, [runPollCycle, stopPolling]);

  const initialLoad = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const next = await api.getOrchestration(caseId);
      if (!mountedRef.current) return;
      setStatus(next);
      void enrichTasks(next);
      if (isInFlight(next)) beginPolling();
    } catch (error) {
      if (mountedRef.current) setLoadError(getVietnameseApiError(error));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [api, caseId, beginPolling, enrichTasks]);

  useEffect(() => {
    mountedRef.current = true;
    void initialLoad();
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [initialLoad, stopPolling]);

  const advance = useCallback(async () => {
    setAdvanceError(null);
    setAdvancing(true);
    setLiveMessage("Đã tiếp nhận yêu cầu. Đang khởi động bước tiếp theo…");
    try {
      const accepted = await api.advanceOrchestration(caseId);
      if (!mountedRef.current) return;
      setLiveMessage(
        accepted.created
          ? "Đã tiếp nhận yêu cầu. Đang xếp lịch xử lý…"
          : "Bước điều phối đã được xếp lịch trước đó. Đang cập nhật trạng thái…",
      );
      await refreshSilently();
      if (!mountedRef.current) return;
      beginPolling();
    } catch (error) {
      if (!mountedRef.current) return;
      setAdvancing(false);
      setAdvanceError(advanceErrorMessage(error));
      setLiveMessage("Không thể tiến hành bước tiếp theo.");
    }
  }, [api, caseId, beginPolling, refreshSilently]);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Đang tải quy trình xử lý"
        className="case-skeleton"
        role="status"
      >
        <span className="skeleton-line skeleton-line-wide" />
        <span className="skeleton-line" />
      </div>
    );
  }

  if (loadError || status === null) {
    return (
      <>
        <CaseNav caseId={caseId} current="quy-trinh" />
        <div className="state-panel" role="alert">
          <p>{loadError ?? "Không thể đọc quy trình xử lý của hồ sơ."}</p>
          <button
            className="button button-secondary"
            onClick={() => void initialLoad()}
            type="button"
          >
            Thử tải lại
          </button>
        </div>
      </>
    );
  }

  const notStarted = status.tasks.length === 0;
  const hasReady = status.readiness.some((stage) => stage.readiness === "READY");

  return (
    <>
      <CaseNav caseId={caseId} current="quy-trinh" />
      <div className="page-heading">
        <p className="eyebrow">Điều phối hồ sơ · phiên bản {status.caseVersion}</p>
        <h1>Quy trình xử lý</h1>
        <p>
          Phòng điều khiển của hồ sơ: theo dõi các bước chuyên môn, cổng phê
          duyệt của con người và hàng đợi điều phối. Hệ thống chỉ chuẩn bị và rà
          soát chứng cứ — mọi quyết định tín dụng do con người đưa ra.
        </p>
      </div>

      <section
        aria-label="Điều khiển quy trình"
        className={`${styles.controlBar} ${notStarted ? styles.controlBarInvite : ""}`}
      >
        <div className={styles.controlCopy}>
          <p className={styles.controlEyebrow}>Bước tiếp theo</p>
          <p className={styles.controlHint}>
            {advanceHint({ notStarted, hasReady, status, advancing })}
          </p>
        </div>
        <button
          className="button button-primary"
          disabled={advancing}
          onClick={() => void advance()}
          type="button"
        >
          {advancing
            ? "Đang tiến hành…"
            : notStarted
              ? "Khởi động quy trình"
              : "Tiến hành bước tiếp theo"}
        </button>
      </section>

      <p aria-live="polite" className={styles.liveRegion} role="status">
        {liveMessage}
      </p>

      {advanceError ? (
        <div className={styles.inlineAlert} role="alert">
          <p>{advanceError}</p>
          <button
            className="button button-secondary button-small"
            onClick={() => void advance()}
            type="button"
          >
            Thử lại
          </button>
        </div>
      ) : null}

      {refreshError ? (
        <p className={styles.refreshNote} role="status">
          Không cập nhật được trạng thái mới nhất ({refreshError}). Vẫn hiển thị
          dữ liệu gần nhất.
        </p>
      ) : null}

      {status.deadlock ? <DeadlockPanel reasons={status.deadlock.reasons} /> : null}

      <div className={styles.columns}>
        <StageRail
          caseVersion={status.caseVersion}
          gates={status.gates}
          readiness={status.readiness}
          taskDetails={taskDetails}
          tasks={status.tasks}
        />
        <PlannerQueue plan={status.plan} planSource={status.planSource} />
      </div>
    </>
  );
}

function DeadlockPanel({ reasons }: { reasons: string[] }) {
  return (
    <section aria-labelledby="deadlock-heading" className={styles.deadlock} role="status">
      <p className={styles.deadlockEyebrow}>Quy trình đang tạm dừng</p>
      <h2 className={styles.deadlockTitle} id="deadlock-heading">
        Không có bước nào có thể chạy ngay
      </h2>
      <p className={styles.deadlockLead}>
        Công việc còn lại đang chờ một cổng phê duyệt của con người hoặc chờ xử
        lý khoảng trống chứng cứ. Đây là điểm dừng có chủ đích, không phải lỗi hệ
        thống.
      </p>
      <ul className={styles.deadlockReasons}>
        {reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
    </section>
  );
}

function advanceHint({
  notStarted,
  hasReady,
  status,
  advancing,
}: {
  notStarted: boolean;
  hasReady: boolean;
  status: OrchestrationStatusDto;
  advancing: boolean;
}): string {
  if (advancing) return "Đang xử lý yêu cầu và cập nhật trạng thái quy trình.";
  if (notStarted) {
    return "Quy trình chưa khởi động. Tiến hành để bắt đầu điều phối các bước chuyên môn.";
  }
  if (hasReady) {
    return "Có bước đã đủ điều kiện. Tiến hành để xếp lịch xử lý bước tiếp theo.";
  }
  if (isInFlight(status)) {
    return "Đang có tác vụ được xử lý. Trạng thái sẽ tự cập nhật.";
  }
  return "Chưa có bước nào sẵn sàng để xếp lịch ngay. Kiểm tra các cổng phê duyệt bên dưới.";
}

function isInFlight(status: OrchestrationStatusDto): boolean {
  const taskWorking = status.tasks.some((task) => isTaskInFlight(task.status));
  const readinessWorking = status.readiness.some((stage) =>
    isReadinessInProgress(stage.readiness),
  );
  return taskWorking || readinessWorking;
}

function settledSummary(status: OrchestrationStatusDto): string {
  const counts: Record<string, number> = {};
  for (const stage of status.readiness) {
    counts[stage.readiness] = (counts[stage.readiness] ?? 0) + 1;
  }
  const parts: string[] = [];
  if (counts.COMPLETE) parts.push(`${counts.COMPLETE} bước đạt`);
  if (counts.IN_PROGRESS) parts.push(`${counts.IN_PROGRESS} bước đang xử lý`);
  if (counts.READY) parts.push(`${counts.READY} bước sẵn sàng`);
  if (counts.BLOCKED) parts.push(`${counts.BLOCKED} bước chờ điều kiện`);
  if (counts.FAILED) parts.push(`${counts.FAILED} bước cần rà soát`);
  return parts.length > 0
    ? `Đã cập nhật quy trình: ${parts.join(", ")}.`
    : "Đã cập nhật quy trình.";
}

function advanceErrorMessage(error: unknown): string {
  // Prefer the server's own Vietnamese reason (e.g. a 409 conflict message) so
  // the officer sees exactly what the engine reported.
  if (error instanceof ApiClientError) return error.message;
  return getVietnameseApiError(error);
}
