"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useMemo, useState } from "react";

import type {
  CreditOpsStatus,
  DocumentRequest,
  ProposedAction,
  UpstreamArtifactKind,
} from "../../lib/api/credit-ops";
import {
  ACTION_TYPE_LABELS,
  ARTIFACT_LABELS,
  ARTIFACT_ROUTE_SLUG,
  BLOCKING_LEVEL_LABELS,
  creditOpsApi,
  CreditOpsApiClient,
  formatCount,
  formatDateTime,
  getCreditOpsError,
  isCreditOpsNotAvailable,
  labelFor,
  MEMO_SECTION_LABELS,
  shortId,
} from "../../lib/api/credit-ops";
import { CaseNav } from "../shell/case-nav";
import { EvidenceChip, StatusChip } from "../ui";
import { AuthorizationForm } from "./authorization-form";
import styles from "./credit-ops.module.css";

// The three specialist assessments this bench consolidates, each with the desk
// that owns it. Intake is a preparation input, shown but not part of the rollup.
const ROLLUP_ARTIFACTS: readonly UpstreamArtifactKind[] = [
  "UNDERWRITING_ASSESSMENT",
  "LEGAL_ASSESSMENT",
  "RISK_REVIEW_ASSESSMENT",
];

function isPresent(status: string): boolean {
  return status === "PRESENT";
}

function GateChip({ satisfied }: { satisfied: boolean }) {
  return satisfied ? (
    <StatusChip label="Đạt" status="PASSED" />
  ) : (
    <StatusChip label="Đang chờ" status="PENDING" />
  );
}

export function CreditOpsDesk({
  caseId,
  api = creditOpsApi,
}: {
  caseId: string;
  api?: Pick<
    CreditOpsApiClient,
    "getCreditOps" | "authorizeAction" | "approveDocumentRequest"
  >;
}) {
  const [status, setStatus] = useState<CreditOpsStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notAvailable, setNotAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const load = useCallback(
    async (withSkeleton: boolean) => {
      if (withSkeleton) setLoading(true);
      setLoadError(null);
      setNotAvailable(false);
      try {
        setStatus(await api.getCreditOps(caseId));
      } catch (requestError) {
        if (isCreditOpsNotAvailable(requestError)) {
          setNotAvailable(true);
          setStatus(null);
        } else {
          setLoadError(getCreditOpsError(requestError));
        }
      } finally {
        if (withSkeleton) setLoading(false);
      }
    },
    [api, caseId],
  );

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load(true);
    })();
    return () => {
      active = false;
    };
  }, [load]);

  // Reload after a write. Never throws: a record that succeeded must not look
  // failed just because the follow-up read hiccuped.
  const refresh = useCallback(async () => {
    setRefreshError(null);
    try {
      setStatus(await api.getCreditOps(caseId));
    } catch (requestError) {
      if (!isCreditOpsNotAvailable(requestError)) {
        setRefreshError(getCreditOpsError(requestError));
      }
    }
  }, [api, caseId]);

  const approveRequest = useCallback(
    async (requestId: string, input: { rationale: string }) => {
      await api.approveDocumentRequest(caseId, requestId, input);
      await refresh();
    },
    [api, caseId, refresh],
  );

  const authorizeAction = useCallback(
    async (actionId: string, input: { rationale: string }) => {
      await api.authorizeAction(caseId, actionId, input);
      await refresh();
    },
    [api, caseId, refresh],
  );

  const summary = useMemo(() => {
    if (!status) return null;
    const pendingRequests = status.documentRequests.filter(
      (request) => request.approvalStatus !== "APPROVED",
    );
    const pendingActions = status.proposedActions.filter((action) => !action.authorized);
    const missingArtifacts = status.packageCompleteness.artifacts.filter(
      (item) => !isPresent(item.status),
    );
    const g2Satisfied = status.g2GateStatus === "SATISFIED";
    const g4Satisfied = status.g4GateStatus === "SATISFIED";
    return {
      pendingRequests,
      pendingActions,
      missingArtifacts,
      g2Satisfied,
      g4Satisfied,
      ready: g2Satisfied && g4Satisfied && missingArtifacts.length === 0,
    };
  }, [status]);

  const header = (
    <>
      <CaseNav caseId={caseId} current="tong-hop" />
      <div className={styles.header}>
        <p className={styles.eyebrow}>Vận hành tín dụng · tổng hợp hồ sơ</p>
        <h1 className={styles.pageTitle}>Bàn tổng hợp gói hồ sơ</h1>
        <p className={styles.lede}>
          Nơi lắp ráp cuối cùng của gói hồ sơ: hợp nhất chứng cứ, rà soát tính đầy
          đủ, và ghi ủy quyền của con người cho từng yêu cầu bổ sung tài liệu và
          hành động đề xuất. Hệ thống chỉ chuẩn bị và rà soát chứng cứ; mọi quyết
          định tín dụng do con người thực hiện.
        </p>
      </div>
    </>
  );

  if (loading) {
    return (
      <div className={styles.page}>
        {header}
        <div
          aria-busy="true"
          aria-label="Đang tải gói tổng hợp"
          className="case-skeleton"
          role="status"
        >
          <span className="skeleton-line skeleton-line-wide" />
          <span className="skeleton-line" />
        </div>
      </div>
    );
  }

  if (notAvailable) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>Chưa có gói tổng hợp cho phiên bản hồ sơ này</p>
          <p className={styles.emptyBody}>
            Gói tổng hợp được lắp ráp tự động sau khi cả bốn phần đầu vào đã sẵn
            sàng: bàn giao tiếp nhận, thẩm định tín dụng, pháp chế và rà soát rủi
            ro độc lập. Hãy hoàn tất các phần còn thiếu, rồi quay lại đây.
          </p>
          <div className={styles.emptyLinks}>
            <Link className="button button-secondary" href={`/ho-so/${caseId}/tham-dinh`}>
              Mở thẩm định
            </Link>
            <Link className="button button-secondary" href={`/ho-so/${caseId}/phap-che`}>
              Mở pháp chế
            </Link>
            <Link className="button button-secondary" href={`/ho-so/${caseId}/rui-ro`}>
              Mở rà soát rủi ro
            </Link>
          </div>
          <div className={styles.emptyRetry}>
            <button
              className="button button-secondary"
              onClick={() => void load(true)}
              type="button"
            >
              Thử tải lại
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (loadError || !status || !summary) {
    return (
      <div className={styles.page}>
        {header}
        <div className="state-panel" role="alert">
          <p>{loadError ?? "Không thể đọc gói tổng hợp."}</p>
          <button
            className="button button-secondary"
            onClick={() => void load(true)}
            type="button"
          >
            Thử tải lại
          </button>
        </div>
      </div>
    );
  }

  const { pendingRequests, pendingActions, missingArtifacts, g2Satisfied, g4Satisfied, ready } =
    summary;

  return (
    <div className={`${styles.page} ${styles.reveal}`}>
      {header}

      <dl className={styles.provenance}>
        <div className={styles.provItem}>
          <dt className={styles.provLabel}>Mã gói</dt>
          <dd className={styles.provValue}>{shortId(status.packageId)}</dd>
        </div>
        <div className={styles.provItem}>
          <dt className={styles.provLabel}>Phiên bản hồ sơ</dt>
          <dd className={styles.provValue}>v{status.caseVersion}</dd>
        </div>
        <div className={styles.provItem}>
          <dt className={styles.provLabel}>Phiên bản chỉ dẫn</dt>
          <dd className={styles.provValue}>{status.promptVersion || "—"}</dd>
        </div>
        <div className={styles.provItem}>
          <dt className={styles.provLabel}>Mã thực thi</dt>
          <dd className={styles.provValue}>{shortId(status.executionId)}</dd>
        </div>
        <div className={styles.provItem}>
          <dt className={styles.provLabel}>Thời điểm lắp ráp</dt>
          <dd className={`${styles.provValue} ${styles.provValuePlain}`}>
            {formatDateTime(status.createdAt)}
          </dd>
        </div>
      </dl>

      {/* Overall package status hero — one strong chip + what remains. */}
      <section
        aria-label="Trạng thái tổng thể của gói hồ sơ"
        aria-live="polite"
        className={`${styles.card} ${styles.hero}`}
      >
        <div className={styles.heroHead}>
          <div>
            <p className={styles.heroEyebrow}>Trạng thái gói</p>
            <h2 className={styles.heroTitle}>
              {ready ? "Gói hồ sơ đã sẵn sàng bàn giao" : "Gói hồ sơ chưa hoàn tất"}
            </h2>
          </div>
          <GateChip satisfied={ready} />
        </div>
        {ready ? (
          <p className={styles.heroBody}>
            Mọi yêu cầu bổ sung tài liệu và hành động đề xuất đã được con người ghi
            phê duyệt/ủy quyền, và không thiếu phần đầu vào nào. Có thể chuyển sang
            bước bàn giao cho người ra quyết định tín dụng.
          </p>
        ) : (
          <>
            <p className={styles.heroBody}>Còn lại để hoàn tất gói:</p>
            <ul className={styles.blockerList}>
              {missingArtifacts.length > 0 ? (
                <li>
                  <span className={styles.blockerNum}>{formatCount(missingArtifacts.length)}</span>{" "}
                  phần đầu vào còn thiếu (xem danh sách kiểm tra bên dưới)
                </li>
              ) : null}
              {pendingRequests.length > 0 ? (
                <li>
                  <span className={styles.blockerNum}>{formatCount(pendingRequests.length)}</span>{" "}
                  yêu cầu bổ sung tài liệu chờ phê duyệt (cổng G2)
                </li>
              ) : null}
              {pendingActions.length > 0 ? (
                <li>
                  <span className={styles.blockerNum}>{formatCount(pendingActions.length)}</span>{" "}
                  hành động đề xuất chờ ủy quyền (cổng G4)
                </li>
              ) : null}
              {missingArtifacts.length === 0 &&
              pendingRequests.length === 0 &&
              pendingActions.length === 0 ? (
                <li>Đang chờ cập nhật cổng; hãy tải lại nếu vừa ghi phê duyệt.</li>
              ) : null}
            </ul>
          </>
        )}
        <p className={styles.heroDisclaimer}>
          Đây là gói chứng cứ để trình người ra quyết định; bản thân gói không phải
          là một quyết định tín dụng.
        </p>
      </section>

      {refreshError ? (
        <div className="state-panel" role="alert" style={{ marginBottom: "1.5rem" }}>
          <p>Đã ghi vào sổ, nhưng không tải lại được bản mới nhất: {refreshError}</p>
          <button
            className="button button-secondary"
            onClick={() => void refresh()}
            type="button"
          >
            Tải lại
          </button>
        </div>
      ) : null}

      {/* Analysis / completeness summary. */}
      <section aria-labelledby="tong-hop-completeness" className={styles.card}>
        <header className={styles.cardHead}>
          <div>
            <p className={styles.cardEyebrow}>Rà soát tất định</p>
            <h2 className={styles.cardTitle} id="tong-hop-completeness">
              Tính đầy đủ của gói
            </h2>
          </div>
          <GateChip satisfied={status.packageCompleteness.allRequiredPresent} />
        </header>

        <ul className={styles.checklist}>
          {status.packageCompleteness.artifacts.map((item) => {
            const present = isPresent(item.status);
            const artifactLabel = labelFor(ARTIFACT_LABELS, item.artifact);
            return (
              <li className={styles.checkItem} key={item.artifact}>
                <div className={styles.checkTop}>
                  <span className={styles.checkName}>{artifactLabel}</span>
                  <StatusChip
                    label={present ? "Đã có" : "Chưa có"}
                    status={present ? "PASSED" : "FAILED"}
                  />
                </div>
                <p className={styles.checkDetail}>{item.detailVi}</p>
                {present && item.referenceId ? (
                  <EvidenceChip
                    documentName={artifactLabel}
                    versionLabel={`Mã ${shortId(item.referenceId)}`}
                  />
                ) : null}
              </li>
            );
          })}
        </ul>

        <div className={styles.miniMetrics}>
          <div className={styles.miniMetric}>
            <span className={styles.miniNum}>
              {formatCount(status.packageCompleteness.unresolvedChallengeCount)}
            </span>
            <span className={styles.miniLabel}>Thách thức chưa có quyết định</span>
          </div>
          <div className={styles.miniMetric}>
            <span className={styles.miniNum}>
              {formatCount(status.packageCompleteness.openBlockingGapCount)}
            </span>
            <span className={styles.miniLabel}>Khoảng trống mức chặn còn mở</span>
          </div>
        </div>
        {status.packageCompleteness.dispositionsStateVi ? (
          <p className={styles.dispositionState}>
            {status.packageCompleteness.dispositionsStateVi}
          </p>
        ) : null}
      </section>

      {/* Included assessments rollup — each part links to its own desk. */}
      <section aria-labelledby="tong-hop-assessments" className={styles.card}>
        <header className={styles.cardHead}>
          <div>
            <p className={styles.cardEyebrow}>Các phần thẩm định đã đưa vào</p>
            <h2 className={styles.cardTitle} id="tong-hop-assessments">
              Nguồn đánh giá của gói
            </h2>
          </div>
        </header>
        <ul className={styles.rollup}>
          {ROLLUP_ARTIFACTS.map((artifact) => {
            const item = status.packageCompleteness.artifacts.find(
              (candidate) => candidate.artifact === artifact,
            );
            const entry = status.evidenceConsolidation.entries.find(
              (candidate) => candidate.artifact === artifact,
            );
            const present = item ? isPresent(item.status) : false;
            const slug = ARTIFACT_ROUTE_SLUG[artifact];
            const artifactLabel = labelFor(ARTIFACT_LABELS, artifact);
            return (
              <li className={styles.rollupItem} key={artifact}>
                <div className={styles.rollupMain}>
                  <span className={styles.rollupName}>{artifactLabel}</span>
                  <span className={styles.rollupMeta}>
                    {entry ? formatCount(entry.citationCount) : "0"} trích dẫn chứng cứ
                  </span>
                </div>
                <div className={styles.rollupSide}>
                  <StatusChip
                    label={present ? "Đã có" : "Chưa có"}
                    status={present ? "PASSED" : "FAILED"}
                  />
                  {slug ? (
                    <Link className={styles.rollupLink} href={`/ho-so/${caseId}/${slug}`}>
                      Mở phần này →
                    </Link>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      {/* Evidence completeness — the consolidated provenance index. */}
      <section aria-labelledby="tong-hop-evidence" className={styles.card}>
        <header className={styles.cardHead}>
          <div>
            <p className={styles.cardEyebrow}>Hợp nhất chứng cứ</p>
            <h2 className={styles.cardTitle} id="tong-hop-evidence">
              Chỉ mục xuất xứ
            </h2>
          </div>
          <span className={styles.countPill}>
            {formatCount(status.evidenceConsolidation.distinctCitationCount)} trích dẫn
          </span>
        </header>
        {status.evidenceConsolidation.entries.length === 0 ? (
          <p className={styles.noneRow}>Chưa có mục xuất xứ nào được hợp nhất.</p>
        ) : (
          <div className={styles.tableScroll}>
            <table className={styles.provTable}>
              <caption className={styles.srOnly}>Chỉ mục xuất xứ theo từng phần đầu vào</caption>
              <thead>
                <tr>
                  <th scope="col">Phần đầu vào</th>
                  <th scope="col">Mã bản đánh giá</th>
                  <th scope="col">Mã thực thi</th>
                  <th className={styles.numCol} scope="col">
                    Số trích dẫn
                  </th>
                </tr>
              </thead>
              <tbody>
                {status.evidenceConsolidation.entries.map((entry) => (
                  <tr key={entry.artifact}>
                    <th scope="row">{labelFor(ARTIFACT_LABELS, entry.artifact)}</th>
                    <td className={styles.mono}>{shortId(entry.assessmentId ?? entry.handoffId)}</td>
                    <td className={styles.mono}>{shortId(entry.executionId)}</td>
                    <td className={`${styles.mono} ${styles.numCol}`}>
                      {formatCount(entry.citationCount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Document requests — the G2 human-approval surface. */}
      <section aria-labelledby="tong-hop-requests" className={styles.card}>
        <header className={styles.cardHead}>
          <div>
            <p className={styles.cardEyebrow}>Yêu cầu bổ sung tài liệu · cổng G2</p>
            <h2 className={styles.cardTitle} id="tong-hop-requests">
              Phê duyệt yêu cầu bổ sung
            </h2>
          </div>
          <GateChip satisfied={g2Satisfied} />
        </header>
        <p className={styles.cardHint}>
          Mỗi yêu cầu là bản nháp; hệ thống không gửi đi bất cứ đâu. Cán bộ vận hành
          ghi phê duyệt cho từng yêu cầu. Cổng G2 đạt khi mọi yêu cầu đã được phê
          duyệt.
        </p>
        {status.documentRequests.length === 0 ? (
          <p className={styles.noneRow}>
            Không có yêu cầu bổ sung tài liệu nào; không có gì cần phê duyệt cho gói
            này.
          </p>
        ) : (
          <ul className={styles.recordList}>
            {status.documentRequests.map((request) => (
              <RequestRow key={request.id} onApprove={approveRequest} request={request} />
            ))}
          </ul>
        )}
      </section>

      {/* Proposed actions — the G4 human-authorization surface. */}
      <section aria-labelledby="tong-hop-actions" className={styles.card}>
        <header className={styles.cardHead}>
          <div>
            <p className={styles.cardEyebrow}>Hành động đề xuất · cổng G4</p>
            <h2 className={styles.cardTitle} id="tong-hop-actions">
              Ủy quyền hành động đề xuất
            </h2>
          </div>
          <GateChip satisfied={g4Satisfied} />
        </header>
        <p className={styles.cardHint}>
          Mỗi hành động ở trạng thái nháp và không bao giờ được thực thi ở bất kỳ đâu
          trong hệ thống. Ủy quyền chỉ ghi nhận thẩm quyền của con người. Cổng G4
          đạt khi mọi hành động đã được ủy quyền.
        </p>
        {status.proposedActions.length === 0 ? (
          <p className={styles.noneRow}>Không có hành động đề xuất nào cho gói này.</p>
        ) : (
          <ul className={styles.recordList}>
            {status.proposedActions.map((action) => (
              <ActionRow action={action} key={action.id} onAuthorize={authorizeAction} />
            ))}
          </ul>
        )}
      </section>

      {/* Assembled package manifest. */}
      <section aria-labelledby="tong-hop-manifest" className={styles.card}>
        <header className={styles.cardHead}>
          <div>
            <p className={styles.cardEyebrow}>Gói đã lắp ráp</p>
            <h2 className={styles.cardTitle} id="tong-hop-manifest">
              Bảng kê thành phần gói
            </h2>
          </div>
        </header>
        <div className={styles.manifest}>
          <div className={styles.manifestRow}>
            <span className={styles.manifestFile}>bien-ban-tin-dung.md</span>
            <span className={styles.manifestNote}>
              {status.draftMemo.present ? "Bản nháp biên bản tín dụng" : "Chưa có bản nháp"}
            </span>
          </div>
          {status.draftMemo.present ? (
            <ul className={styles.manifestSub}>
              {status.draftMemo.sections.map((section) => (
                <li className={styles.manifestSubRow} key={section.key}>
                  <span className={styles.manifestSubName}>
                    {labelFor(MEMO_SECTION_LABELS, section.key)}
                  </span>
                  <span className={styles.manifestSubMeta}>
                    {formatCount(section.statementCount)} luận điểm ·{" "}
                    {formatCount(section.citationCount)} trích dẫn
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <div className={styles.manifestRow}>
            <span className={styles.manifestFile}>yeu-cau-bo-sung-tai-lieu</span>
            <span className={styles.manifestNote}>
              {formatCount(status.documentRequests.length)} mục
            </span>
          </div>
          <div className={styles.manifestRow}>
            <span className={styles.manifestFile}>hanh-dong-de-xuat</span>
            <span className={styles.manifestNote}>
              {formatCount(status.proposedActions.length)} mục
            </span>
          </div>
          <div className={styles.manifestRow}>
            <span className={styles.manifestFile}>chi-muc-xuat-xu</span>
            <span className={styles.manifestNote}>
              {formatCount(status.evidenceConsolidation.entries.length)} nguồn ·{" "}
              {formatCount(status.evidenceConsolidation.distinctCitationCount)} trích dẫn
            </span>
          </div>
          <div className={styles.manifestRow}>
            <span className={styles.manifestFile}>danh-sach-kiem-tra</span>
            <span className={styles.manifestNote}>
              {formatCount(status.packageCompleteness.artifacts.length)} mục
            </span>
          </div>
        </div>
        {status.draftMemo.syntheticDisclaimerVi ? (
          <p className={styles.syntheticNote}>{status.draftMemo.syntheticDisclaimerVi}</p>
        ) : null}
      </section>

      {/* Final handoff — only offered when the package is complete. */}
      <section aria-label="Bàn giao gói hồ sơ" className={`${styles.card} ${styles.handoffCard}`}>
        <div>
          <h2 className={styles.cardTitle}>Bàn giao cho người ra quyết định</h2>
          <p className={styles.cardHint}>
            {ready
              ? "Gói đã đầy đủ. Chuyển sang bước bàn giao để trình người ra quyết định tín dụng."
              : "Hoàn tất các mục còn lại ở trên trước khi bàn giao. Liên kết sẽ mở bàn giao khi gói đã đầy đủ."}
          </p>
        </div>
        {ready ? (
          <Link className="button button-primary" href={`/ho-so/${caseId}/ban-giao`}>
            Mở bàn giao →
          </Link>
        ) : (
          <span aria-disabled="true" className={styles.handoffDisabled}>
            Bàn giao (chưa sẵn sàng)
          </span>
        )}
      </section>
    </div>
  );
}

function RequestRow({
  request,
  onApprove,
}: {
  request: DocumentRequest;
  onApprove: (requestId: string, input: { rationale: string }) => Promise<void>;
}) {
  const approved = request.approvalStatus === "APPROVED";
  return (
    <li className={styles.recordItem}>
      <div className={styles.recordTop}>
        <StatusChip
          label={approved ? "Đã phê duyệt" : "Chờ phê duyệt"}
          status={approved ? "PASSED" : "PENDING"}
        />
        <span className={styles.recordTag}>
          Mức: {labelFor(BLOCKING_LEVEL_LABELS, request.blockingLevel)}
        </span>
      </div>
      <p className={styles.recordText}>{request.requestText}</p>
      <EvidenceChip
        documentName="Khoảng trống nguồn gốc"
        versionLabel={`Mã ${shortId(request.originatingGapId)}`}
      />
      {approved ? (
        <div className={styles.recordApprovals}>
          {request.approvals.map((approval) => (
            <p className={styles.recordApproval} key={approval.id}>
              <span className={styles.recordApprovalMeta}>
                {approval.actorRole} · {formatDateTime(approval.createdAt)}
              </span>
              <span className={styles.recordApprovalNote}>{approval.rationale}</span>
            </p>
          ))}
        </div>
      ) : (
        <AuthorizationForm
          hint="Ghi phê duyệt của cán bộ vận hành cho yêu cầu bổ sung này. Việc phê duyệt chỉ ghi nhận vào sổ; không gửi yêu cầu đi đâu."
          onSubmit={(input) => onApprove(request.id, input)}
          rationaleLabel="Lý do phê duyệt"
          submitLabel="Ghi phê duyệt yêu cầu"
        />
      )}
    </li>
  );
}

function ActionRow({
  action,
  onAuthorize,
}: {
  action: ProposedAction;
  onAuthorize: (actionId: string, input: { rationale: string }) => Promise<void>;
}) {
  return (
    <li className={styles.recordItem}>
      <div className={styles.recordTop}>
        <StatusChip
          label={action.authorized ? "Đã ủy quyền" : "Chờ ủy quyền"}
          status={action.authorized ? "PASSED" : "PENDING"}
        />
        <span className={styles.recordTag}>{labelFor(ACTION_TYPE_LABELS, action.actionType)}</span>
      </div>
      <p className={styles.recordText}>{action.description}</p>
      {action.relatedDocumentRequestId ? (
        <EvidenceChip
          documentName="Yêu cầu liên quan"
          versionLabel={`Mã ${shortId(action.relatedDocumentRequestId)}`}
        />
      ) : null}
      {action.authorized ? (
        <div className={styles.recordApprovals}>
          {action.authorizations.map((authorization) => (
            <p className={styles.recordApproval} key={authorization.id}>
              <span className={styles.recordApprovalMeta}>
                {authorization.actorRole} · {formatDateTime(authorization.createdAt)}
              </span>
              <span className={styles.recordApprovalNote}>{authorization.rationale}</span>
            </p>
          ))}
        </div>
      ) : (
        <AuthorizationForm
          hint="Ghi ủy quyền của cán bộ vận hành cho hành động đề xuất này. Ủy quyền chỉ ghi nhận thẩm quyền; không có bước thực thi ở bất kỳ đâu."
          onSubmit={(input) => onAuthorize(action.id, input)}
          rationaleLabel="Lý do ủy quyền"
          submitLabel="Ghi ủy quyền hành động"
        />
      )}
    </li>
  );
}
