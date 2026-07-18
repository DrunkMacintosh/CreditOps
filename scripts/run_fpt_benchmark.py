"""Run the committed synthetic holdout against a live managed FPT endpoint.

This script PRODUCES benchmark evidence; it never activates a route. Like
``scripts/smoke_fpt.py`` it builds its catalog through the explicitly named
``FPTCatalog.for_benchmark_evaluation`` path — the only constructor allowed to
reach an endpoint before a pass record exists — and it uses the same
``SKIP``/``PASS``/``FAIL`` house style:

* ``SKIP`` when a capability's endpoint is not fully configured, so a local run
  can never be mistaken for live-provider evidence;
* ``PASS`` when the holdout meets its PROPOSED threshold: the script writes
  ``docs/benchmarks/<capability>-<model>-evidence.md`` (no secrets) and PRINTS
  the exact ready-to-commit ``FPTBenchmarkRecord(...)`` plus a DECISION_LOG row;
* ``FAIL`` otherwise: it prints the failing cases, writes evidence marked
  FAILED, and emits NO pass record — the route stays DISABLED.

The script NEVER edits ``benchmark_records.py``. A human reviews the evidence and
commits the record. All holdout data is synthetic (dữ liệu tổng hợp).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

from creditops.benchmarks.evidence import (
    evidence_filename,
    render_decision_log_row,
    render_evidence_markdown,
    render_record_snippet,
)
from creditops.benchmarks.runner import (
    CapabilityReport,
    run_embedding_benchmark,
    run_reasoning_benchmark,
)
from creditops.infrastructure.fpt.catalog import CapabilityName, FPTCatalog
from creditops.infrastructure.fpt.client import FPTClient
from creditops.infrastructure.fpt.gateway import FPTInferenceGateway
from creditops.infrastructure.fpt.model_catalog import FPT_MODEL_CATALOG

_DEFAULT_OUT_DIR = "docs/benchmarks"
_BENCHMARKABLE: tuple[CapabilityName, ...] = ("reasoning", "embedding")


def _capability_catalog(capability: CapabilityName) -> FPTCatalog:
    """Build an evaluation catalog scoped to ONE capability.

    The committed model catalog pins several capabilities (reasoning, vision,
    embedding), and ``for_benchmark_evaluation`` requires every pinned capability
    to be fully configured. Scoping the model catalog and environment to the one
    capability under test lets a user benchmark, say, reasoning without also
    configuring a vision endpoint — while the committed model id stays the sole
    authority. Raises ``ValueError`` when this capability is not fully configured.
    """

    pinned = FPT_MODEL_CATALOG.get(capability)
    if pinned is None:
        raise ValueError(f"no model is pinned in code for {capability}")
    prefix = f"FPT_{capability.upper()}_"
    environ = {
        key: value
        for key, value in os.environ.items()
        if key == "FPT_API_KEY" or key.startswith(prefix)
    }
    return FPTCatalog.for_benchmark_evaluation(
        model_catalog={capability: pinned},
        environ=environ,
    )


async def _run_capability(
    capability: CapabilityName,
    *,
    out_dir: str,
    recorded_on: str,
) -> int:
    try:
        catalog = _capability_catalog(capability)
        config = catalog.config_for(capability)
    except (ValueError, KeyError) as exc:
        print(f"SKIP: FPT {capability} endpoint is not fully configured ({exc})")
        return 0
    client = FPTClient(catalog)
    try:
        gateway = FPTInferenceGateway(catalog, client, max_attempts=1)
        identity = {
            "model_id": config.model_id,
            "endpoint_id": config.endpoint_id,
            "route_version": catalog.route_version,
            "prompt_version": catalog.prompt_version,
            "schema_version": catalog.schema_version,
        }
        if capability == "reasoning":
            report = await run_reasoning_benchmark(gateway, **identity)
        else:
            report = await run_embedding_benchmark(gateway, **identity)
    except Exception as exc:  # noqa: BLE001 - surface any live failure clearly
        print(
            f"FAIL: FPT {capability} benchmark could not run ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    finally:
        await client.close()
    return _emit(report, out_dir=out_dir, recorded_on=recorded_on)


def _emit(report: CapabilityReport, *, out_dir: str, recorded_on: str) -> int:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    filename = evidence_filename(report)
    evidence_path = out_path / filename
    evidence_path.write_text(render_evidence_markdown(report), encoding="utf-8")
    ref = f"{out_dir.rstrip('/')}/{filename}"

    if not report.passed:
        print(
            f"FAIL: FPT {report.capability} benchmark scored "
            f"{report.passed_count}/{report.total} (< threshold {report.threshold:.2f})",
            file=sys.stderr,
        )
        for result in report.failed_results:
            print(f"  - {result.case_id} [{result.kind}]: {result.reason}", file=sys.stderr)
        print(f"      FAILED evidence written: {evidence_path}", file=sys.stderr)
        print("      No benchmark-pass record emitted; the route stays DISABLED.", file=sys.stderr)
        return 1

    print(
        f"PASS: FPT {report.capability} benchmark scored "
        f"{report.passed_count}/{report.total} (>= threshold {report.threshold:.2f}) "
        f"model={report.model_id} endpoint={report.endpoint_id}"
    )
    print(f"      evidence written: {evidence_path}")
    print()
    print("=== Ready-to-commit record (a human adds this to benchmark_records.py) ===")
    print(render_record_snippet(report, recorded_on=recorded_on, evidence_ref=ref))
    print()
    print("=== DECISION_LOG.md row template ===")
    print(render_decision_log_row(report, recorded_on=recorded_on, evidence_ref=ref))
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capability",
        choices=(*_BENCHMARKABLE, "all"),
        default="all",
        help="Which capability holdout to run (default: all configured).",
    )
    parser.add_argument(
        "--out-dir",
        default=_DEFAULT_OUT_DIR,
        help="Directory for the evidence artefact (default: docs/benchmarks).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    recorded_on = date.today().isoformat()
    if args.capability == "all":
        capabilities: tuple[CapabilityName, ...] = _BENCHMARKABLE
    else:
        capabilities = (args.capability,)
    exit_code = 0
    for capability in capabilities:
        code = asyncio.run(
            _run_capability(capability, out_dir=args.out_dir, recorded_on=recorded_on)
        )
        exit_code = max(exit_code, code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
