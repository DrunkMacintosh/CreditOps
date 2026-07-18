import React from "react";

import type {
  OrchestrationGateDto,
  OrchestrationReadinessDto,
  OrchestrationTaskDto,
  TaskDetailDto,
} from "../../lib/api/orchestration";
import { EvidenceChip, StatusChip } from "./chips";
import {
  gateStatusDescriptor,
  gateTypeLabel,
  isReadinessInProgress,
  isTaskInFlight,
  readinessDescriptor,
  taskStatusDescriptor,
  taskTypeLabel,
  translateReason,
  type ChipTone,
} from "./labels";
import styles from "./orchestration.module.css";

// The human gate that must be SATISFIED before each specialist stage may become
// ready — mirrors the canonical dependency template in
// services/api/src/creditops/application/orchestration/graph.py.
const REQUIRED_GATE: Record<string, string> = {
  CREDIT_UNDERWRITING: "G1_INTAKE_COMPLETE",
  LEGAL_COMPLIANCE_COLLATERAL: "G1_INTAKE_COMPLETE",
  INDEPENDENT_RISK_REVIEW: "G2_GAP_REQUEST_APPROVAL",
  CREDIT_OPERATIONS: "G3_RISK_DISPOSITION",
};

const MARKER_TONE_CLASS: Record<ChipTone, string> = {
  ok: styles.markerOk,
  amber: styles.markerAmber,
  info: styles.markerInfo,
  risk: styles.markerRisk,
  muted: styles.markerMuted,
};

export function StageRail({
  readiness,
  tasks,
  gates,
  caseVersion,
  taskDetails,
}: {
  readiness: OrchestrationReadinessDto[];
  tasks: OrchestrationTaskDto[];
  gates: OrchestrationGateDto[];
  caseVersion: number;
  taskDetails: Record<string, TaskDetailDto>;
}) {
  const gateByType = new Map(gates.map((gate) => [gate.gateType, gate]));

  return (
    <section aria-labelledby="stage-rail-heading" className={styles.card}>
      <div className={styles.cardHead}>
        <p className={styles.eyebrow}>Đồ thị xử lý</p>
        <h2 className={styles.cardTitle} id="stage-rail-heading">
          Các bước chuyên môn
        </h2>
        <p className={styles.cardLead}>
          Mỗi bước hiển thị điều kiện sẵn sàng và lý do đằng sau — đây là chuỗi
          chứng cứ của quy trình. Bước chỉ tự động xếp lịch khi đã đủ điều kiện;
          quyết định vẫn thuộc về con người.
        </p>
      </div>

      <ol className={styles.rail}>
        {readiness.map((stage, index) => {
          const descriptor = readinessDescriptor(stage.readiness);
          const requiredGateType = REQUIRED_GATE[stage.taskType];
          const requiredGate = requiredGateType
            ? gateByType.get(requiredGateType)
            : undefined;
          const task = pickTask(tasks, stage.taskType, caseVersion);
          const detail = task ? taskDetails[task.taskId] : undefined;

          return (
            <li className={styles.stageItem} key={stage.taskType}>
              <span
                aria-hidden="true"
                className={`${styles.stageMarker} ${MARKER_TONE_CLASS[descriptor.tone]}`}
              />
              <div className={styles.stageBody}>
                <div className={styles.stageHeader}>
                  <span className={styles.stageIndex}>Bước {index + 1}</span>
                  <h3 className={styles.stageName}>{taskTypeLabel(stage.taskType)}</h3>
                  <StatusChip
                    label={descriptor.label}
                    tone={descriptor.tone}
                    pulse={isReadinessInProgress(stage.readiness)}
                  />
                </div>

                <div className={styles.stageWhy}>
                  <EvidenceChip
                    reference="điều-kiện"
                    label="Nguồn: bộ máy điều phối"
                  />
                  <p className={styles.reasonText}>{translateReason(stage.reason)}</p>
                </div>

                {requiredGate ? (
                  <GateLine gate={requiredGate} />
                ) : null}

                {task ? <TaskLine task={task} detail={detail} /> : null}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function GateLine({ gate }: { gate: OrchestrationGateDto }) {
  const descriptor = gateStatusDescriptor(gate.status);
  return (
    <div className={styles.stageMeta}>
      <span className={styles.stageMetaLabel}>Cổng phụ thuộc</span>
      <span className={styles.stageMetaValue}>{gateTypeLabel(gate.gateType)}</span>
      <StatusChip label={descriptor.label} tone={descriptor.tone} />
      {gate.dispositionRef ? (
        <EvidenceChip
          reference={gate.dispositionRef}
          detail={gate.satisfiedAt ? formatTime(gate.satisfiedAt) : undefined}
          label="Chứng cứ phê duyệt cổng"
        />
      ) : null}
    </div>
  );
}

function TaskLine({
  task,
  detail,
}: {
  task: OrchestrationTaskDto;
  detail: TaskDetailDto | undefined;
}) {
  const descriptor = taskStatusDescriptor(task.status);
  return (
    <div className={styles.stageMeta}>
      <span className={styles.stageMetaLabel}>Tác vụ xử lý</span>
      <StatusChip
        label={descriptor.label}
        tone={descriptor.tone}
        pulse={isTaskInFlight(task.status)}
      />
      {detail ? (
        <span className={styles.taskAttempts}>
          Lần thử {detail.attemptCount}/{detail.maxAttempts}
        </span>
      ) : null}
      {detail?.checkpoint ? (
        <EvidenceChip
          reference={detail.checkpoint.checkpointType}
          detail={`#${detail.checkpoint.sequenceNo}`}
          label="Điểm lưu tiến độ gần nhất"
        />
      ) : null}
    </div>
  );
}

// Choose the representative task for a stage at the current case version: a live
// (non-superseded) attempt if one exists, otherwise the most recent row.
function pickTask(
  tasks: OrchestrationTaskDto[],
  taskType: string,
  caseVersion: number,
): OrchestrationTaskDto | undefined {
  const current = tasks.filter(
    (task) => task.taskType === taskType && task.caseVersion === caseVersion,
  );
  if (current.length === 0) return undefined;
  return current.find((task) => task.status !== "SUPERSEDED") ?? current[current.length - 1];
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("vi-VN");
}
