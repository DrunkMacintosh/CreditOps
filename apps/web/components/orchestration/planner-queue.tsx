import React from "react";

import type { OrchestrationPlanStepDto } from "../../lib/api/orchestration";
import { EvidenceChip } from "./chips";
import {
  planSourceLabel,
  planSourceRef,
  taskTypeLabel,
} from "./labels";
import styles from "./orchestration.module.css";

// The planner proposes an ordering over the tasks the deterministic engine has
// already declared READY. It can reprioritise but never invent or unblock work,
// so an empty queue means nothing is schedulable right now — not a failure.
export function PlannerQueue({
  plan,
  planSource,
}: {
  plan: OrchestrationPlanStepDto[];
  planSource: string;
}) {
  return (
    <section aria-labelledby="planner-queue-heading" className={styles.card}>
      <div className={styles.cardHead}>
        <p className={styles.eyebrow}>Hàng đợi điều phối</p>
        <h2 className={styles.cardTitle} id="planner-queue-heading">
          Đề xuất thứ tự xử lý
        </h2>
        <div className={styles.planSource}>
          <EvidenceChip
            reference={planSourceRef(planSource)}
            label="Nguồn của thứ tự xử lý"
          />
          <span className={styles.planSourceText}>{planSourceLabel(planSource)}</span>
        </div>
      </div>

      {plan.length === 0 ? (
        <p className={styles.queueEmpty}>
          Hiện chưa có bước nào sẵn sàng để xếp lịch. Các bước sẽ xuất hiện tại
          đây khi đủ điều kiện phụ thuộc và cổng phê duyệt.
        </p>
      ) : (
        <ol className={styles.queue}>
          {plan.map((step, index) => (
            <li className={styles.queueCard} key={step.taskType}>
              <span className={styles.queueOrder}>{index + 1}</span>
              <div className={styles.queueBody}>
                <p className={styles.queueTask}>{taskTypeLabel(step.taskType)}</p>
                <p className={styles.queueRationale}>{rationale(planSource)}</p>
              </div>
              <span className={styles.queuePriority} title="Thứ tự ưu tiên">
                ưu tiên {step.priority}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function rationale(planSource: string): string {
  if (planSource === "LLM_PROPOSED") {
    return "Ưu tiên do trợ lý điều phối đề xuất và đã qua kiểm chứng của bộ máy.";
  }
  return "Thứ tự chuẩn theo đồ thị phụ thuộc của quy trình.";
}
