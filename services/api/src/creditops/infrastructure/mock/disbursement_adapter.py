"""Labelled deterministic mock disbursement execution adapter — NOT real
core-banking execution.

Master design section 5 giai đoạn 11: "Deterministic mock adapter thực thi;
không có real core-banking execution."  Given an action id + idempotency key this
returns a DETERMINISTIC receipt: the same inputs always produce the same
``receipt_ref`` (a ``uuid5`` over the adapter label + action + key), so a
duplicate delivery can never move money twice and tests are reproducible without
any network call.  Every receipt is stamped ``is_mock=True`` and the fixed
``MOCK_DISBURSEMENT_ADAPTER_LABEL`` so it can never be mistaken for a real
provider.

The constructor flag ``simulate_unknown`` makes the adapter return an
``EXECUTION_UNKNOWN`` result (a simulated timeout / lost response) so the human
reconciliation path can be exercised.  It defaults to ``False`` -- an unknown
result is NEVER the default behaviour.
"""

from __future__ import annotations

from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from creditops.domain.disbursements import (
    MOCK_DISBURSEMENT_ADAPTER_LABEL,
    DisbursementExecutionReceipt,
    ExecutionStatus,
)


class MockDisbursementExecutionAdapter:
    """Fixture-free deterministic mock — not a production disbursement."""

    label = MOCK_DISBURSEMENT_ADAPTER_LABEL

    def __init__(self, *, simulate_unknown: bool = False) -> None:
        #: Test-only: when True the adapter simulates a timeout / ambiguous
        #: response (records EXECUTION_UNKNOWN).  NEVER defaulted on.
        self._simulate_unknown = simulate_unknown

    def execute(
        self, *, action_id: UUID, idempotency_key: str
    ) -> DisbursementExecutionReceipt:
        """Return a deterministic receipt for one execution attempt.

        On a normal run the result is ``CONFIRMED_EXECUTED`` with a deterministic
        ``receipt_ref``.  With ``simulate_unknown`` the result is
        ``EXECUTION_UNKNOWN`` with no receipt -- the caller must NOT blindly retry
        it; only a human reconciliation may resolve it.
        """

        if self._simulate_unknown:
            return DisbursementExecutionReceipt(
                id=uuid4(),
                action_id=action_id,
                idempotency_key=idempotency_key,
                adapter_label=MOCK_DISBURSEMENT_ADAPTER_LABEL,
                result_status=ExecutionStatus.EXECUTION_UNKNOWN,
                receipt_ref=None,
                is_mock=True,
            )
        receipt_ref = str(
            uuid5(
                NAMESPACE_OID,
                f"{MOCK_DISBURSEMENT_ADAPTER_LABEL}:{action_id}:{idempotency_key}",
            )
        )
        return DisbursementExecutionReceipt(
            id=uuid4(),
            action_id=action_id,
            idempotency_key=idempotency_key,
            adapter_label=MOCK_DISBURSEMENT_ADAPTER_LABEL,
            result_status=ExecutionStatus.CONFIRMED_EXECUTED,
            receipt_ref=receipt_ref,
            is_mock=True,
        )


__all__ = ["MockDisbursementExecutionAdapter"]
