"""The canonical synthetic-data notice is a safety contract (AGENTS.md).

``shared/synthetic-notice.json`` is the single source of truth; the backend
constants must match it exactly so the frontend, the Credit Operations memo,
and the domain schema can never drift apart again (master design P0 #10).
"""

from __future__ import annotations

import json
from pathlib import Path

from creditops.domain.credit_ops import SYNTHETIC_DISCLAIMER_VI, DraftCreditMemo
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_EN, SYNTHETIC_NOTICE_VI

_REPO_ROOT = Path(__file__).resolve().parents[5]


def _shared_notice() -> dict[str, str]:
    payload = json.loads((_REPO_ROOT / "shared" / "synthetic-notice.json").read_text("utf-8"))
    return {"en": payload["en"], "vi": payload["vi"]}


def test_backend_notice_constants_match_the_shared_source_of_truth() -> None:
    shared = _shared_notice()
    assert SYNTHETIC_NOTICE_EN == shared["en"]
    assert SYNTHETIC_NOTICE_VI == shared["vi"]


def test_memo_disclaimer_embeds_the_canonical_vietnamese_notice() -> None:
    # The mandatory memo header must carry the canonical accented notice; the
    # additional "not a credit decision" guard sentence may follow it but can
    # never replace it.
    assert SYNTHETIC_DISCLAIMER_VI.startswith(SYNTHETIC_NOTICE_VI)
    assert "KHÔNG PHẢI là một quyết định tín dụng" in SYNTHETIC_DISCLAIMER_VI


def test_memo_schema_literal_matches_the_pinned_disclaimer() -> None:
    # The Literal annotation on the schema field and the module constant must
    # be the same exact string, so a schema instance built with the constant
    # validates and any other value is rejected.
    field = DraftCreditMemo.model_fields["synthetic_disclaimer_vi"]
    assert field.default == SYNTHETIC_DISCLAIMER_VI
