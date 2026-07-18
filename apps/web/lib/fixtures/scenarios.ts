// -----------------------------------------------------------------------------
// The 12 synthetic test scenarios (brief §8) — metadata + pass/fail evaluators.
//
// Metadata is static and drives the switcher panel. Evaluators read the live
// FixtureStore so "actual" reflects what the tester has done via real workspace
// CTAs or the deterministic Test controls. Nothing here is live agent inference.
// -----------------------------------------------------------------------------

import type { FixtureStore } from "./store";
import type { AssertionResult, ScenarioDefinition, ScenarioId } from "./types";

export const SCENARIOS: readonly ScenarioDefinition[] = [
  {
    id: "clean-complete",
    ordinal: 1,
    title: "Hồ sơ hoàn chỉnh, sạch",
    initialState: "Đủ tài liệu, không mâu thuẫn, không khoảng trống; đã bàn giao sang thẩm định.",
    agentUnderTest: "Agent tiếp nhận & trích xuất tài liệu",
    humanGate: "HG_INTAKE_COMPLETE — Cán bộ tiếp nhận xác nhận hoàn tất",
    expectedResult: "Toàn bộ candidate fact được xác nhận, không còn conflict/gap, handoff READY_FOR_SPECIALIST_REVIEW.",
    evidenceRefs: ["ev-cf-doanh-thu", "ev-cf-loi-nhuan", "ev-cf-tong-tai-san"],
    auditEvent: "INTAKE_COMPLETED",
    focusSection: "tiep-nhan",
    testControls: [],
  },
  {
    id: "missing-documents",
    ordinal: 2,
    title: "Thiếu tài liệu",
    initialState: "Có báo cáo tài chính nhưng thiếu sao kê và hợp đồng đầu ra → còn khoảng trống.",
    agentUnderTest: "Agent phát hiện khoảng trống chứng cứ",
    humanGate: "HG_OUTBOUND_REQUEST_APPROVED — Duyệt yêu cầu bổ sung tài liệu",
    expectedResult: "Không thể hoàn tất tiếp nhận (canCompleteIntake=false); khoảng trống được liệt kê rõ.",
    evidenceRefs: ["gap-sao-ke", "gap-hop-dong-dau-ra"],
    auditEvent: "INTAKE_INCOMPLETE_BLOCKED",
    focusSection: "khoang-trong",
    testControls: [
      {
        id: "attempt-complete",
        label: "Thử hoàn tất tiếp nhận",
        effect: "Gọi intake-completion → nhận 409 INTAKE_INCOMPLETE với danh sách lý do.",
      },
    ],
  },
  {
    id: "conflicting-facts",
    ordinal: 3,
    title: "Dữ kiện mâu thuẫn",
    initialState: "Doanh thu 2025 khác nhau giữa báo cáo tài chính và tờ khai thuế.",
    agentUnderTest: "Agent đối chiếu chứng cứ",
    humanGate: "HG_INTAKE_COMPLETE — Cán bộ xử lý mâu thuẫn trước khi hoàn tất",
    expectedResult: "Conflict hiển thị đủ ≥2 nguồn; chặn hoàn tất tiếp nhận cho đến khi con người xử lý.",
    evidenceRefs: ["conf-doanh-thu"],
    auditEvent: "CONFLICT_DETECTED",
    focusSection: "doi-chieu",
    testControls: [],
  },
  {
    id: "document-quality",
    ordinal: 4,
    title: "Tài liệu trùng / hết hạn / không đọc được",
    initialState: "Một bản tải trùng, một sao kê scan mờ không trích xuất được.",
    agentUnderTest: "Agent kiểm tra chất lượng tài liệu",
    humanGate: "HG_INTAKE_COMPLETE — Cán bộ định đoạt UNREADABLE/ABSENT",
    expectedResult: "Tài liệu không đọc được được đánh dấu, không tạo candidate fact giả.",
    evidenceRefs: ["doc-scan-mo"],
    auditEvent: "DOCUMENT_UNREADABLE_FLAGGED",
    focusSection: "tai-lieu",
    focusDocumentId: "doc-scan-mo",
    testControls: [
      {
        id: "mark-unreadable",
        label: "Đánh dấu không đọc được",
        effect: "Ghi disposition UNREADABLE cho tài liệu scan mờ (không sinh candidate).",
      },
    ],
  },
  {
    id: "risk-challenge",
    ordinal: 5,
    title: "Rủi ro phản biện, maker phải chỉnh sửa",
    initialState: "Independent Risk Review nêu challenge yêu cầu maker sửa đề xuất.",
    agentUnderTest: "Agent rà soát rủi ro độc lập (checker)",
    humanGate: "Định đoạt challenge rủi ro (MAKER_MUST_REVISE)",
    expectedResult: "Challenge ở trạng thái MAKER_MUST_REVISE không thể tự đóng; cần maker phản hồi.",
    evidenceRefs: ["chal-dscr", "maker-response"],
    auditEvent: "RISK_CHALLENGE_DISPOSED",
    focusSection: "rui-ro",
    testControls: [
      {
        id: "dispose-must-revise",
        label: "Định đoạt: MAKER_MUST_REVISE",
        effect: "Ghi nhận challenge yêu cầu sửa; gói đề xuất KHÔNG được finalize.",
      },
    ],
  },
  {
    id: "downstream-stale",
    ordinal: 6,
    title: "Bằng chứng mới làm hạ nguồn lỗi thời",
    initialState: "Bằng chứng mới ở version 5 khiến một fact xác nhận ở version 4 bị stale.",
    agentUnderTest: "Agent quản lý lineage / version",
    humanGate: "HG_INTAKE_COMPLETE — Rà soát lại fact bị stale",
    expectedResult: "Fact stale được đánh dấu rõ (không chỉ dựa màu); yêu cầu xem lại trước khi dùng.",
    evidenceRefs: ["ev-cf-doanh-thu"],
    auditEvent: "EVIDENCE_SUPERSEDED",
    focusSection: "tham-dinh",
    testControls: [],
  },
  {
    id: "policy-unavailable",
    ordinal: 7,
    title: "Nguồn chính sách không khả dụng",
    initialState: "Dịch vụ tra cứu chính sách/pháp chế đang gián đoạn.",
    agentUnderTest: "Agent pháp chế (tra cứu chính sách)",
    humanGate: "Không có gate; hệ thống phải fail-closed",
    expectedResult: "Màn pháp chế hiển thị UNAVAILABLE với hành động tiếp theo, không bịa kết quả chính sách.",
    evidenceRefs: [],
    auditEvent: "POLICY_SOURCE_UNAVAILABLE",
    focusSection: "phap-che",
    testControls: [],
  },
  {
    id: "unauthorized-access",
    ordinal: 8,
    title: "Cán bộ không có quyền / truy cập chéo hồ sơ",
    initialState: "Người dùng hiện tại không phải cán bộ được phân công hồ sơ.",
    agentUnderTest: "Lớp kiểm soát quyền (capability/actor)",
    humanGate: "Không áp dụng",
    expectedResult: "Không render control mutation (capabilities=false); thao tác ép buộc trả 403.",
    evidenceRefs: [],
    auditEvent: "AUTHORIZATION_DENIED",
    focusSection: "tiep-nhan",
    testControls: [
      {
        id: "force-mutation",
        label: "Ép gọi thao tác bị cấm",
        effect: "Gửi confirm dù không có quyền → nhận 403; giao diện không lộ tài nguyên.",
      },
      {
        id: "cross-case",
        label: "Thử mở hồ sơ không được phân công",
        effect: "GET case trả 404 — không tiết lộ hồ sơ có tồn tại hay không.",
      },
    ],
  },
  {
    id: "pending-condition",
    ordinal: 9,
    title: "Điều kiện giải ngân còn treo",
    initialState: "Một điều kiện tiên quyết chưa đủ bằng chứng; chưa được mở giải ngân.",
    agentUnderTest: "Agent theo dõi điều kiện giải ngân",
    humanGate: "Waiver là hành động của con người (không tự động)",
    expectedResult: "Không mở giải ngân khi còn điều kiện PENDING; chỉ con người mới waive.",
    evidenceRefs: ["cond-bao-hiem-tai-san"],
    auditEvent: "DISBURSEMENT_BLOCKED_PENDING_CONDITION",
    focusSection: "dieu-kien-giai-ngan",
    testControls: [],
  },
  {
    id: "execution-unknown",
    ordinal: 10,
    title: "EXECUTION_UNKNOWN khi giải ngân",
    initialState: "Lệnh giải ngân (mock) trả kết quả không xác định, cần đối soát.",
    agentUnderTest: "Agent điều phối giải ngân (mock, dual-auth)",
    humanGate: "Ủy quyền kép + đối soát EXECUTION_UNKNOWN",
    expectedResult: "Trạng thái EXECUTION_UNKNOWN hiển thị rõ với đường đối soát; không giả định thành công.",
    evidenceRefs: ["disb-attempt-1"],
    auditEvent: "DISBURSEMENT_EXECUTION_UNKNOWN",
    focusSection: "giai-ngan",
    testControls: [
      {
        id: "reconcile",
        label: "Bắt đầu đối soát",
        effect: "Chuyển trạng thái sang đang đối soát; vẫn chưa xác nhận chuyển tiền.",
      },
    ],
  },
  {
    id: "repayment-anomaly",
    ordinal: 11,
    title: "Trả nợ một phần / trễ / đảo",
    initialState: "Sổ cái ghi nhận khoản trả một phần, trễ hạn và một bút toán đảo.",
    agentUnderTest: "Agent đối soát thu nợ (pure-fold ledger)",
    humanGate: "Định đoạt cách xử lý khoản bất thường",
    expectedResult: "Ledger thập phân chính xác; nhận diện trùng/đảo/sai thứ tự; đề xuất hành động cho con người.",
    evidenceRefs: ["pmt-partial-1", "pmt-reversal-1"],
    auditEvent: "REPAYMENT_ANOMALY_RECORDED",
    focusSection: "thu-no",
    testControls: [],
  },
  {
    id: "settlement-recovery",
    ordinal: 12,
    title: "Tất toán và leo thang thu hồi",
    initialState: "Còn nghĩa vụ chưa hoàn tất; cần recovery evidence pack, không thể đóng hồ sơ.",
    agentUnderTest: "Agent tất toán / xử lý nợ",
    humanGate: "Gate riêng cho hành động thu hồi",
    expectedResult: "Không đóng hồ sơ khi còn nghĩa vụ; hành động recovery yêu cầu human gate riêng.",
    evidenceRefs: ["obl-goc-con-lai"],
    auditEvent: "SETTLEMENT_BLOCKED_OPEN_OBLIGATION",
    focusSection: "tat-toan-xu-ly-no",
    testControls: [],
  },
];

export function getScenario(id: ScenarioId): ScenarioDefinition {
  const found = SCENARIOS.find((s) => s.id === id);
  if (!found) throw new Error(`Unknown scenario: ${id}`);
  return found;
}

// Evaluators read the live store; default to "pending" until the tester has
// exercised the relevant gate/CTA. "pass" means the observed state matches the
// expected safe behaviour; "fail" means an invariant was violated.
type Evaluator = (store: FixtureStore) => AssertionResult;

const EVALUATORS: Record<ScenarioId, Evaluator> = {
  "clean-complete": (s) => {
    if (s.conflicts.length > 0) {
      return { status: "fail", actual: `Còn ${s.conflicts.length} mâu thuẫn chưa xử lý.` };
    }
    if (s.intakeComplete && s.handoff) {
      return { status: "pass", actual: `Đã bàn giao (${s.handoff.state}), không còn mâu thuẫn.` };
    }
    return { status: "pending", actual: "Chưa hoàn tất tiếp nhận." };
  },
  "missing-documents": (s) => {
    if (s.intakeComplete) {
      return { status: "fail", actual: "Đã hoàn tất tiếp nhận dù còn thiếu tài liệu." };
    }
    return {
      status: s.case.capabilities.canCompleteIntake ? "fail" : "pass",
      actual: s.case.capabilities.canCompleteIntake
        ? "canCompleteIntake=true dù còn khoảng trống."
        : "Bị chặn hoàn tất (canCompleteIntake=false).",
    };
  },
  "conflicting-facts": (s) => {
    if (s.conflicts.length === 0) {
      return { status: "pending", actual: "Chưa nạp mâu thuẫn." };
    }
    const enoughSources = s.conflicts.every((c) => c.sources.length >= 2);
    return {
      status: enoughSources && !s.intakeComplete ? "pass" : "fail",
      actual: enoughSources
        ? `Hiển thị ${s.conflicts.length} mâu thuẫn, mỗi mâu thuẫn ≥2 nguồn; chưa hoàn tất.`
        : "Mâu thuẫn thiếu nguồn hoặc đã hoàn tất sai.",
    };
  },
  "document-quality": (s) => {
    const unreadable = [...s.documents.values()].find((d) => d.candidates.length === 0);
    if (!unreadable) return { status: "pending", actual: "Chưa có tài liệu không đọc được." };
    return {
      status: "pass",
      actual: `Tài liệu ${unreadable.documentId} không sinh candidate fact giả.`,
    };
  },
  "risk-challenge": (s) => {
    const disp = s.getSlice<{ disposition?: string }>("risk");
    if (!disp?.disposition) return { status: "pending", actual: "Chưa định đoạt challenge." };
    return {
      status: disp.disposition === "MAKER_MUST_REVISE" ? "pass" : "fail",
      actual: `Challenge disposition = ${disp.disposition}.`,
    };
  },
  "downstream-stale": (s) => {
    const stale = s.evidence.filter((e) => e.stale);
    return {
      status: stale.length > 0 ? "pass" : "pending",
      actual: stale.length > 0 ? `${stale.length} fact được đánh dấu stale.` : "Chưa có fact stale.",
    };
  },
  "policy-unavailable": (s) => ({
    status: s.flags.policyUnavailable ? "pass" : "fail",
    actual: s.flags.policyUnavailable
      ? "Nguồn chính sách UNAVAILABLE; hệ thống fail-closed."
      : "Nguồn chính sách vẫn khả dụng.",
  }),
  "unauthorized-access": (s) => ({
    status: !s.case.capabilities.canConfirm ? "pass" : "fail",
    actual: !s.case.capabilities.canConfirm
      ? "capabilities=false; control mutation bị ẩn."
      : "Vẫn còn quyền mutation.",
  }),
  "pending-condition": (s) => {
    const cond = s.getSlice<{ opened?: boolean }>("conditions");
    return {
      status: cond?.opened ? "fail" : "pass",
      actual: cond?.opened
        ? "Đã mở giải ngân dù điều kiện còn treo."
        : "Giải ngân bị chặn vì còn điều kiện PENDING.",
    };
  },
  "execution-unknown": (s) => {
    const disb = s.getSlice<{ state?: string }>("disbursement");
    if (!disb?.state) return { status: "pending", actual: "Chưa tạo lệnh giải ngân." };
    return {
      status: disb.state === "EXECUTION_UNKNOWN" || disb.state === "RECONCILING" ? "pass" : "fail",
      actual: `Trạng thái giải ngân = ${disb.state} (không giả định thành công).`,
    };
  },
  "repayment-anomaly": (s) => {
    const rep = s.getSlice<{ recognized?: boolean }>("repayment");
    return {
      status: rep?.recognized ? "pass" : "pending",
      actual: rep?.recognized
        ? "Ledger nhận diện khoản một phần/đảo với số thập phân chính xác."
        : "Chưa nạp bút toán bất thường.",
    };
  },
  "settlement-recovery": (s) => {
    const set = s.getSlice<{ closed?: boolean }>("settlement");
    return {
      status: set?.closed ? "fail" : "pass",
      actual: set?.closed
        ? "Đã đóng hồ sơ dù còn nghĩa vụ."
        : "Không thể đóng hồ sơ khi còn nghĩa vụ mở.",
    };
  },
};

export function evaluateScenario(id: ScenarioId, store: FixtureStore): AssertionResult {
  return EVALUATORS[id](store);
}
