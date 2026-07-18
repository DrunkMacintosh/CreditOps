"""Committed synthetic benchmark harness for FPT capability routes.

This package holds the reviewed, versioned holdout that the evaluation harness
runs against a live managed FPT endpoint to PRODUCE benchmark evidence. It is
the only committed benchmark surface; it is independent of any untracked local
evaluation scratch. Running it never activates a route by itself -- a human
still commits an ``FPTBenchmarkRecord`` before any capability flips ACTIVE (see
``creditops.infrastructure.fpt.benchmark_records`` and
``scripts/run_fpt_benchmark.py``).

All data in this package is synthetic. Toàn bộ dữ liệu khách hàng, chính sách,
tài liệu và phản hồi hệ thống ngân hàng trong dự án này là dữ liệu tổng hợp,
được tạo riêng cho mục đích trình diễn.
"""

from __future__ import annotations

from creditops.benchmarks.holdout import (
    ALL_CASES,
    EMBEDDING_CASES,
    REASONING_CASES,
    EmbeddingCase,
    HoldoutCase,
    ReasoningCase,
    ScoreOutcome,
    embedding_cases,
    reasoning_cases,
)

__all__ = [
    "ALL_CASES",
    "EMBEDDING_CASES",
    "REASONING_CASES",
    "EmbeddingCase",
    "HoldoutCase",
    "ReasoningCase",
    "ScoreOutcome",
    "embedding_cases",
    "reasoning_cases",
]
