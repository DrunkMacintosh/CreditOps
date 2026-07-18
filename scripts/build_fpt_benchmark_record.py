"""Mechanically re-derive a ready-to-commit ``FPTBenchmarkRecord(...)`` literal.

``scripts/run_fpt_benchmark.py`` already prints a ready-to-commit record at the
moment a live run PASSES, but that console output is easy to lose (a rotated
CI log, a reviewer looking only at the uploaded artifact). This script
reconstructs the exact same literal from the **committed evidence artefact**
itself — the file ``creditops.benchmarks.evidence.render_evidence_markdown``
writes to ``docs/benchmarks/<capability>-<model>-evidence.md`` — so that
turning a real benchmark pass into a pasted-in record is a small, mechanical,
reviewable step instead of a hand-typed one.

It reads ONLY the evidence file plus the explicit ``--capability``,
``--model-id``, ``--endpoint-id`` and ``--recorded-on`` given on the command
line; the ``route_version``/``prompt_version``/``schema_version`` on the
printed record always come from the committed
``creditops.infrastructure.fpt.catalog`` module, never from the file or from
an argument. ``recorded_on`` is always the date passed with ``--recorded-on``
— this script never reads the system clock, so the record's date reflects a
human decision, not whenever this happened to run.

This script REFUSES (non-zero exit, nothing printed to stdout) unless:

* the evidence file can be parsed (a ``.md`` file in the
  ``render_evidence_markdown`` format, or a ``.json`` file carrying the same
  identity fields);
* its own declared ``Verdict`` is ``PASS`` (exit 1 — "the evidence does not
  indicate a pass") ;
* its declared capability/model/endpoint/route/prompt/schema identity agrees
  with what was asked for on the command line and with the versions currently
  committed in ``catalog.py`` (exit 2 — evidence does not match the requested
  record, most likely stale evidence or a copy-paste mistake).

It never edits ``benchmark_records.py`` itself and never emits a speculative
record for a run that did not pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from creditops.infrastructure.fpt.benchmark_records import FPTBenchmarkRecord
from creditops.infrastructure.fpt.catalog import (
    PROMPT_VERSION,
    ROUTE_VERSION,
    SCHEMA_VERSION,
    CapabilityName,
)

_RECORDED_ON_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_MD_FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "capability": re.compile(r"^- Capability: `([^`]+)`\s*$", re.MULTILINE),
    "model_id": re.compile(r"^- Model: `([^`]+)`\s*$", re.MULTILINE),
    "endpoint_id": re.compile(r"^- Endpoint id: `([^`]+)`\s*$", re.MULTILINE),
    "route_version": re.compile(r"^- Route version: `([^`]+)`\s*$", re.MULTILINE),
    "prompt_version": re.compile(r"^- Prompt version: `([^`]+)`\s*$", re.MULTILINE),
    "schema_version": re.compile(r"^- Schema version: `([^`]+)`\s*$", re.MULTILINE),
}
_MD_VERDICT_PATTERN = re.compile(r"^- Verdict: \*\*(PASS|FAILED)\*\*\s*$", re.MULTILINE)

_JSON_REQUIRED_FIELDS = (
    "capability",
    "model_id",
    "endpoint_id",
    "route_version",
    "prompt_version",
    "schema_version",
)


class EvidenceError(Exception):
    """The evidence artefact cannot be trusted to build a benchmark record."""


@dataclass(frozen=True)
class _EvidenceIdentity:
    """The non-secret identity + verdict this script trusts from one file."""

    capability: str
    model_id: str
    endpoint_id: str
    route_version: str
    prompt_version: str
    schema_version: str
    passed: bool


def _parse_markdown_evidence(text: str) -> _EvidenceIdentity:
    fields: dict[str, str] = {}
    for name, pattern in _MD_FIELD_PATTERNS.items():
        match = pattern.search(text)
        if match is None:
            raise EvidenceError(
                f"could not find the {name!r} field in the markdown evidence "
                "(expected the format produced by "
                "creditops.benchmarks.evidence.render_evidence_markdown)"
            )
        fields[name] = match.group(1)
    verdict_match = _MD_VERDICT_PATTERN.search(text)
    if verdict_match is None:
        raise EvidenceError(
            "could not find a '- Verdict: **PASS**' or '- Verdict: **FAILED**' line "
            "in the markdown evidence"
        )
    return _EvidenceIdentity(
        capability=fields["capability"],
        model_id=fields["model_id"],
        endpoint_id=fields["endpoint_id"],
        route_version=fields["route_version"],
        prompt_version=fields["prompt_version"],
        schema_version=fields["schema_version"],
        passed=verdict_match.group(1) == "PASS",
    )


def _parse_json_evidence(text: str) -> _EvidenceIdentity:
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"evidence file is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("JSON evidence must be a JSON object")
    missing = [key for key in _JSON_REQUIRED_FIELDS if key not in payload]
    if missing:
        raise EvidenceError(
            f"JSON evidence is missing required field(s): {', '.join(missing)}"
        )
    if "passed" in payload:
        passed_value = payload["passed"]
        if not isinstance(passed_value, bool):
            raise EvidenceError("JSON evidence field 'passed' must be a boolean")
        passed = passed_value
    elif "verdict" in payload:
        verdict = payload["verdict"]
        if verdict not in ("PASS", "FAILED"):
            raise EvidenceError(
                f"JSON evidence field 'verdict' must be 'PASS' or 'FAILED', got {verdict!r}"
            )
        passed = verdict == "PASS"
    else:
        raise EvidenceError(
            "JSON evidence must include a boolean 'passed' field or a 'verdict' field"
        )
    return _EvidenceIdentity(
        capability=str(payload["capability"]),
        model_id=str(payload["model_id"]),
        endpoint_id=str(payload["endpoint_id"]),
        route_version=str(payload["route_version"]),
        prompt_version=str(payload["prompt_version"]),
        schema_version=str(payload["schema_version"]),
        passed=passed,
    )


def _load_evidence(evidence_path: Path) -> _EvidenceIdentity:
    if not evidence_path.is_file():
        raise EvidenceError(f"evidence file not found: {evidence_path}")
    text = evidence_path.read_text(encoding="utf-8")
    suffix = evidence_path.suffix.lower()
    if suffix == ".json":
        return _parse_json_evidence(text)
    if suffix in {".md", ".markdown"}:
        return _parse_markdown_evidence(text)
    raise EvidenceError(
        f"unsupported evidence file extension {suffix!r}; expected '.md' or '.json'"
    )


def _check_identity_matches(
    identity: _EvidenceIdentity,
    *,
    capability: str,
    model_id: str,
    endpoint_id: str,
) -> None:
    expected = {
        "capability": (identity.capability, capability),
        "model_id": (identity.model_id, model_id),
        "endpoint_id": (identity.endpoint_id, endpoint_id),
        "route_version": (identity.route_version, ROUTE_VERSION),
        "prompt_version": (identity.prompt_version, PROMPT_VERSION),
        "schema_version": (identity.schema_version, SCHEMA_VERSION),
    }
    mismatches = [
        f"{field}: evidence says {evidence_value!r}, expected {expected_value!r}"
        for field, (evidence_value, expected_value) in expected.items()
        if evidence_value != expected_value
    ]
    if mismatches:
        raise EvidenceError(
            "evidence identity does not match the requested record (stale evidence, "
            "a moved route/prompt/schema version, or the wrong capability/model/"
            "endpoint was given):\n  " + "\n  ".join(mismatches)
        )


def _render_record_literal(record: FPTBenchmarkRecord) -> str:
    """Match ``creditops.benchmarks.evidence.render_record_snippet`` byte-for-byte."""

    return (
        "FPTBenchmarkRecord(\n"
        f"    capability={record.capability!r},\n"
        f"    model_id={record.model_id!r},\n"
        f"    endpoint_id={record.endpoint_id!r},\n"
        f"    route_version={record.route_version!r},\n"
        f"    prompt_version={record.prompt_version!r},\n"
        f"    schema_version={record.schema_version!r},\n"
        f"    passed={record.passed!r},\n"
        f"    evidence_ref={record.evidence_ref!r},\n"
        f"    recorded_on={record.recorded_on!r},\n"
        ")"
    )


def _repo_relative(path: Path) -> str:
    """Repository-relative POSIX path, resolved against this script's own location."""

    repo_root = Path(__file__).resolve().parent.parent
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise EvidenceError(
            f"evidence path must be inside the repository root ({repo_root}); "
            f"got {resolved}"
        ) from exc


def _recorded_on(value: str) -> str:
    if not _RECORDED_ON_PATTERN.match(value):
        raise argparse.ArgumentTypeError(
            f"--recorded-on must be YYYY-MM-DD (got {value!r}); pass the date the "
            "run was recorded as a literal value — this script never reads the "
            "system clock"
        )
    return value


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "evidence_path",
        help=(
            "Path to the committed evidence artefact produced by "
            "scripts/run_fpt_benchmark.py, e.g. "
            "docs/benchmarks/reasoning-DeepSeek-V4-Flash-evidence.md (or a .json "
            "file carrying the same identity fields). Must be inside the repo."
        ),
    )
    parser.add_argument(
        "--capability",
        required=True,
        choices=get_args(CapabilityName),
        help=(
            "Capability the record is for; must match the evidence file's "
            "own declared capability."
        ),
    )
    parser.add_argument(
        "--model-id",
        required=True,
        help="Model id the record is for; must match the evidence file's own declared model.",
    )
    parser.add_argument(
        "--endpoint-id",
        required=True,
        help="Endpoint id the record is for; must match the evidence file's own declared endpoint.",
    )
    parser.add_argument(
        "--recorded-on",
        required=True,
        type=_recorded_on,
        help=(
            "ISO date (YYYY-MM-DD) to record as recorded_on -- a human "
            "decision, not a clock read."
        ),
    )
    return parser.parse_args(argv)


def _build(args: argparse.Namespace) -> int:
    evidence_path = Path(args.evidence_path)
    evidence_ref = _repo_relative(evidence_path)
    identity = _load_evidence(evidence_path)
    _check_identity_matches(
        identity,
        capability=args.capability,
        model_id=args.model_id,
        endpoint_id=args.endpoint_id,
    )
    if not identity.passed:
        print(
            f"REFUSE: evidence at {evidence_ref} does not indicate a PASS "
            "(its Verdict is FAILED); no benchmark-pass record can be emitted. "
            "The route stays DISABLED.",
            file=sys.stderr,
        )
        return 1
    try:
        record = FPTBenchmarkRecord(
            capability=args.capability,
            model_id=args.model_id,
            endpoint_id=args.endpoint_id,
            route_version=ROUTE_VERSION,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            passed=True,
            evidence_ref=evidence_ref,
            recorded_on=args.recorded_on,
        )
    except ValidationError as exc:
        raise EvidenceError(f"resulting record failed validation: {exc}") from exc
    print(
        f"# evidence {evidence_ref} verified PASS; paste the record below into "
        "FPT_BENCHMARK_RECORDS in "
        "services/api/src/creditops/infrastructure/fpt/benchmark_records.py",
        file=sys.stderr,
    )
    print(_render_record_literal(record))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _build(args)
    except EvidenceError as exc:
        print(f"REFUSE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
