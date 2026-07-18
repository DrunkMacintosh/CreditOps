"""``creditops.log_redaction.LogRedactor`` applied to payload shapes from the
newer G2 / intake / audit modules.

``test_log_redaction.py`` already proves the general contract (sensitive
KEYS are fully redacted; sensitive PATTERNS -- bearer tokens, URL
credentials, AWS-signed-URL parameters, embedded DSNs -- are redacted inside
free-text VALUES; everything else passes through unchanged). These tests
extend that same, unweakened contract to the representative payload shapes
produced by the newer modules:

- outbox event rows / task envelopes (``application/ports/orchestration.py``,
  dispatched by ``api/gap_requests.py`` and ``api/intake.py``'s retick paths),
- gap-request-batch items and dispositions
  (``domain/gap_request_batches.py``, whose ``request_text_vi`` /
  ``rationale_vi`` / ``edited_texts`` originate from untrusted document
  content per ``application/gaps/assembler.py``),
- intake-completion "unresolved reasons" (``api/intake.py``'s 409
  ``INTAKE_INCOMPLETE`` ``details.reasons``),
- audit-event rows (``api/audit.py``'s ``event_data``, documented there as
  "never a secret, credential, or prompt" -- these tests are the
  defense-in-depth backstop for that claim).

By design none of these payloads is SUPPOSED to carry a real secret. Each test
below still injects a secret-or-URL-credential-shaped string into the payload
to prove the redactor would catch it if one ever leaked in -- and, just as
important, that ordinary long Vietnamese business text in the same payload
survives completely untouched (the contract must not be weakened into
blanket-redacting anything long or foreign-scripted).
"""

from __future__ import annotations

from uuid import uuid4

from creditops.log_redaction import LogRedactor

CASE_ID = str(uuid4())
TASK_ID = str(uuid4())
BATCH_ID = str(uuid4())
ITEM_ID = str(uuid4())

#: A long (~600 char), entirely benign Vietnamese business-document sentence
#: with no secret- or URL-shaped substrings anywhere in it.
LONG_BENIGN_TEXT = (
    "Đề nghị khách hàng bổ sung sao kê tài khoản ngân hàng trong vòng sáu "
    "tháng gần nhất, kèm theo báo cáo tài chính đã được kiểm toán độc lập, "
    "hợp đồng thuê mặt bằng nhà xưởng còn hiệu lực, giấy chứng nhận quyền sử "
    "dụng đất đối với tài sản thế chấp, và bản kê khai công nợ hiện tại với "
    "các tổ chức tín dụng khác nếu có, nhằm phục vụ công tác thẩm định hồ sơ "
    "vay vốn lưu động phục vụ hoạt động sản xuất kinh doanh nông sản của "
    "doanh nghiệp trong quý tiếp theo, đảm bảo hồ sơ được xử lý đầy đủ và "
    "đúng tiến độ theo quy trình nội bộ hiện hành."
) * 2


def _redactor() -> LogRedactor:
    return LogRedactor()


# -- outbox / task-envelope payloads ------------------------------------------


def test_outbox_event_row_with_key_named_secret_field_is_redacted() -> None:
    # Mirrors application/ports/orchestration.py::OutboxEventRow plus a
    # hypothetical enriched debug payload some future change might add.
    event = {
        "event_id": str(uuid4()),
        "case_id": CASE_ID,
        "case_version": 1,
        "event_type": "TASK_READY",
        "payload": {
            "schema_version": "1",
            "task_id": TASK_ID,
            "case_id": CASE_ID,
            "case_version": 1,
            "task_type": "DOCUMENT_INGESTION",
            "document_version_id": None,
            "headers": {"Authorization": "Bearer queue-secret-token"},
        },
        "dispatch_attempts": 0,
        "dispatched_at": None,
    }

    cleaned = _redactor().clean(event)

    assert cleaned["payload"]["headers"]["Authorization"] == "[REDACTED]"
    # Plain identifiers are never touched -- they are not secrets.
    assert cleaned["event_id"] == event["event_id"]
    assert cleaned["payload"]["task_id"] == TASK_ID
    assert cleaned["payload"]["case_id"] == CASE_ID
    assert cleaned["event_type"] == "TASK_READY"


def test_orchestration_retick_log_context_with_embedded_bearer_is_redacted() -> None:
    # Mirrors the log_event(...) context built in api/gap_requests.py's
    # _retick_orchestration / api/intake.py's _dispatch_outbox failure paths:
    # {"event": ..., "trigger": trigger_ref}. trigger_ref is normally just
    # "G2:{batch_id}" -- this proves that even if a future change embedded
    # richer diagnostic text there, an accidental secret would still be caught.
    context = {
        "event": "orchestration_retick_failed",
        "trigger": f"G2:{BATCH_ID}",
        "diagnostic": "upstream call failed with Authorization: Bearer worker-secret-abc",
    }

    cleaned = _redactor().clean(context)

    assert "worker-secret-abc" not in cleaned["diagnostic"]
    assert cleaned["trigger"] == f"G2:{BATCH_ID}"
    assert cleaned["event"] == "orchestration_retick_failed"


# -- gap-request-batch items / dispositions -----------------------------------


def test_gap_request_item_with_embedded_dsn_in_request_text_is_redacted() -> None:
    # request_text_vi is built from untrusted, document-derived
    # missing_information_vi / suggested_evidence_vi (application/gaps/
    # assembler.py). A document could plausibly echo back what looks like a
    # connection string; it must never reach logs unredacted.
    item = {
        "id": ITEM_ID,
        "gap_id": str(uuid4()),
        "request_text_vi": (
            "Đề nghị bổ sung bằng chứng liên quan đến tài khoản, tham chiếu "
            "postgresql://dbuser:dbpassword@db.internal:5432/creditops để đối chiếu."
        ),
        "blocking_level": "BLOCKING",
    }

    cleaned = _redactor().clean(item)

    text = cleaned["request_text_vi"]
    assert "dbpassword" not in text
    assert "Đề nghị bổ sung bằng chứng" in text
    assert "để đối chiếu." in text


def test_gap_request_disposition_rationale_with_embedded_signed_url_is_redacted() -> None:
    disposition = {
        "id": str(uuid4()),
        "batch_id": BATCH_ID,
        "disposition_type": "APPROVED_WITH_CHANGES",
        "item_dispositions": {ITEM_ID: "EDITED"},
        "edited_texts": {
            ITEM_ID: (
                "Xem tài liệu tại "
                "https://storage.example.test/private.pdf?"
                "X-Amz-Security-Token=session-secret&business_id=customer-42"
            )
        },
        "actor_id": str(uuid4()),
        "actor_role": "INTAKE_OFFICER",
        "rationale_vi": "Đã chỉnh sửa nội dung yêu cầu theo phản hồi của khách hàng.",
    }

    cleaned = _redactor().clean(disposition)

    edited = cleaned["edited_texts"][ITEM_ID]
    assert "session-secret" not in edited
    assert "customer-42" not in edited
    assert "Xem tài liệu tại" in edited
    # A rationale with no secret-shaped content is untouched.
    assert cleaned["rationale_vi"] == disposition["rationale_vi"]
    # Structural fields (never free text) are untouched.
    assert cleaned["disposition_type"] == "APPROVED_WITH_CHANGES"
    assert cleaned["item_dispositions"] == {ITEM_ID: "EDITED"}


def test_long_benign_gap_request_text_survives_redaction_byte_for_byte() -> None:
    item = {"id": ITEM_ID, "request_text_vi": LONG_BENIGN_TEXT, "blocking_level": "BLOCKING"}

    cleaned = _redactor().clean(item)

    assert cleaned["request_text_vi"] == LONG_BENIGN_TEXT
    assert len(cleaned["request_text_vi"]) == len(LONG_BENIGN_TEXT)


# -- intake-completion unresolved reasons --------------------------------------


def test_intake_incomplete_reasons_list_with_embedded_bearer_is_redacted() -> None:
    # api/intake.py's 409 INTAKE_INCOMPLETE response carries
    # details={"reasons": [...], "unresolvedCount": N} -- free-form reason
    # strings that, again, are not supposed to carry secrets but must be
    # defended if one ever does.
    details = {
        "reasons": [
            "Còn 2 candidate fact chưa được xác nhận cho hồ sơ.",
            "Tài liệu tham chiếu có Authorization: Bearer leaked-reason-token chưa xử lý.",
        ],
        "unresolvedCount": 2,
    }

    cleaned = _redactor().clean(details)

    assert cleaned["reasons"][0] == details["reasons"][0]
    assert "leaked-reason-token" not in cleaned["reasons"][1]
    assert "Tài liệu tham chiếu có" in cleaned["reasons"][1]
    assert cleaned["unresolvedCount"] == 2


def test_intake_incomplete_reasons_with_embedded_aws_signed_url_is_redacted() -> None:
    details = {
        "reasons": [
            (
                "GET /storage/v1/object/sign/private.pdf?"
                "X-Amz-Security-Token=session-secret&business_id=customer-7&limit=20"
            )
        ]
    }

    cleaned = _redactor().clean(details)

    reason = cleaned["reasons"][0]
    assert "session-secret" not in reason
    assert "customer-7" not in reason
    assert "limit=20" in reason


# -- audit-event rows ----------------------------------------------------------


def test_audit_event_data_with_key_named_secret_is_redacted() -> None:
    # api/audit.py documents event_data as passing through as-is because
    # writers never store a secret there. This is the backstop: IF one ever
    # did (a regression), the shared redactor must still catch it before any
    # logging path emits it.
    event = {
        "id": str(uuid4()),
        "case_id": CASE_ID,
        "event_type": "TASK_READY_DISPATCHED",
        "actor_type": "AGENT",
        "actor_id": None,
        "event_data": {"note": "queued", "api_key": "provider-secret-value"},
    }

    cleaned = _redactor().clean(event)

    assert cleaned["event_data"]["api_key"] == "[REDACTED]"
    assert cleaned["event_data"]["note"] == "queued"
    assert cleaned["event_type"] == "TASK_READY_DISPATCHED"


def test_audit_event_data_with_embedded_bearer_in_free_text_is_redacted() -> None:
    event = {
        "event_data": {
            "detail": "dispatch retried after Bearer audit-secret-abc failed once",
        }
    }

    cleaned = _redactor().clean(event)

    assert "audit-secret-abc" not in cleaned["event_data"]["detail"]
    assert "dispatch retried after" in cleaned["event_data"]["detail"]
