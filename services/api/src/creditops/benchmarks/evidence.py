"""Render benchmark evidence, a pass-record snippet, and a decision-log row.

These functions turn a :class:`~creditops.benchmarks.runner.CapabilityReport`
into committed-artefact text. They deliberately consume ONLY the report's
non-secret identity (model id, endpoint id, versions) — never an API key or
endpoint URL — so nothing here can leak a secret into a committed file.

The rendered ``FPTBenchmarkRecord(...)`` snippet is text for a human to paste
into ``creditops.infrastructure.fpt.benchmark_records`` in a reviewed change; a
pass record is refused for a FAILED run. Nothing here edits that registry.

All evidence describes runs over synthetic data. Toàn bộ dữ liệu là dữ liệu tổng
hợp, được tạo riêng cho mục đích trình diễn.
"""

from __future__ import annotations

import re

from creditops.benchmarks.runner import CapabilityReport

_DEFAULT_OUT_DIR = "docs/benchmarks"

_SYNTHETIC_NOTICE = (
    "Dữ liệu tổng hợp — mọi hồ sơ, chính sách và phản hồi đều được tạo riêng cho "
    "mục đích trình diễn (synthetic data only)."
)


def _safe_model(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model_id).strip("-") or "model"


def evidence_filename(report: CapabilityReport) -> str:
    """Canonical ``<capability>-<model>-evidence.md`` file name."""

    return f"{report.capability}-{_safe_model(report.model_id)}-evidence.md"


def evidence_ref(report: CapabilityReport, *, out_dir: str = _DEFAULT_OUT_DIR) -> str:
    """Repository-relative pointer to the committed evidence artefact."""

    return f"{out_dir.rstrip('/')}/{evidence_filename(report)}"


def render_evidence_markdown(report: CapabilityReport) -> str:
    """Human-readable evidence for one capability run (no secrets)."""

    status = "PASS" if report.passed else "FAILED"
    lines = [
        f"# FPT {report.capability} benchmark evidence — {status}",
        "",
        f"> {_SYNTHETIC_NOTICE}",
        "",
        "This artefact records one evaluation run of the committed synthetic holdout",
        "against a live managed FPT endpoint. It is not itself an activation: a human",
        "must review it and commit a matching `FPTBenchmarkRecord` before the route",
        "leaves DISABLED.",
        "",
        "## Run identity",
        "",
        f"- Capability: `{report.capability}`",
        f"- Model: `{report.model_id}`",
        f"- Endpoint id: `{report.endpoint_id}`",
        f"- Route version: `{report.route_version}`",
        f"- Prompt version: `{report.prompt_version}`",
        f"- Schema version: `{report.schema_version}`",
        f"- Run started (UTC): `{report.started_at.isoformat()}`",
        "",
        "## Result",
        "",
        f"- Cases passed: **{report.passed_count} / {report.total}**",
        f"- Score: **{report.score:.3f}**",
        f"- PROPOSED threshold: **{report.threshold:.3f}**",
        f"- Verdict: **{status}**",
        "",
        "## Per-case outcomes",
        "",
        "| Case | Kind | Result | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for result in report.results:
        verdict = "PASS" if result.passed else "FAIL"
        reason = result.reason.replace("|", "\\|")
        lines.append(f"| `{result.case_id}` | {result.kind} | {verdict} | {reason} |")
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            "- Harness: `scripts/run_fpt_benchmark.py` via",
            "  `FPTCatalog.for_benchmark_evaluation` (the evaluation-only path).",
            "- Secrets: none. Only the non-secret model id, endpoint id and",
            "  route/prompt/schema versions appear here; the API key and endpoint URL",
            "  are never rendered.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def render_record_snippet(
    report: CapabilityReport, *, recorded_on: str, evidence_ref: str
) -> str:
    """The exact ``FPTBenchmarkRecord(...)`` a human commits (pass runs only)."""

    if not report.passed:
        raise ValueError(
            "refusing to render a benchmark-pass record for a FAILED run; "
            "the route must stay DISABLED"
        )
    return (
        "FPTBenchmarkRecord(\n"
        f"    capability={report.capability!r},\n"
        f"    model_id={report.model_id!r},\n"
        f"    endpoint_id={report.endpoint_id!r},\n"
        f"    route_version={report.route_version!r},\n"
        f"    prompt_version={report.prompt_version!r},\n"
        f"    schema_version={report.schema_version!r},\n"
        "    passed=True,\n"
        f"    evidence_ref={evidence_ref!r},\n"
        f"    recorded_on={recorded_on!r},\n"
        ")"
    )


def render_decision_log_row(
    report: CapabilityReport, *, recorded_on: str, evidence_ref: str
) -> str:
    """A DECISION_LOG.md table-row template for the recorded run."""

    decision = (
        f"Record FPT {report.capability} benchmark-pass for model "
        f"`{report.model_id}` (endpoint `{report.endpoint_id}`) binding "
        f"route/prompt/schema `{report.route_version}`/`{report.prompt_version}`/"
        f"`{report.schema_version}`; evidence `{evidence_ref}`."
    )
    reason = (
        f"Synthetic Vietnamese-banking holdout scored {report.passed_count}/"
        f"{report.total} (>= PROPOSED threshold {report.threshold:.2f}); routes stay "
        "gated on committed evidence."
    )
    alternatives = (
        "Leave the route DISABLED (no record); record without committed evidence; "
        "relax the threshold"
    )
    conditions = (
        "Review when the model, endpoint, or route/prompt/schema versions change, "
        "or when an official benchmark set supersedes the synthetic holdout."
    )
    return (
        f"| {recorded_on} | {decision} | {reason} | {alternatives} | PROPOSED | "
        f"{conditions} |"
    )
