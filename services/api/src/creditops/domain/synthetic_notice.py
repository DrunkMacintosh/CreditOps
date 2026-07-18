"""Canonical synthetic-data notice (AGENTS.md, Non-negotiable boundaries).

``shared/synthetic-notice.json`` at the repository root is the single source
of truth; tests on every surface (backend, frontend, deck) assert their
pinned constants equal it exactly.  Change the wording only through a
reviewed governance decision recorded in ``docs/DECISION_LOG.md``.
"""

from __future__ import annotations

from typing import Final

SYNTHETIC_NOTICE_EN: Final = (
    "All customer data, policies, documents, and banking-system responses "
    "in this project are synthetic and created solely for demonstration."
)

SYNTHETIC_NOTICE_VI: Final = (
    "Toàn bộ dữ liệu khách hàng, chính sách, tài liệu và phản hồi hệ thống "
    "ngân hàng trong dự án này là dữ liệu tổng hợp, được tạo riêng cho mục "
    "đích trình diễn."
)
