"""(d) A proposed action cannot self-execute: port-surface and source proofs.

``CreditOpsRepository`` is the ENTIRE durable-state surface the credit-ops
use case and worker processor may call.  These tests prove, independent of
any concrete adapter, that:

- the Protocol exposes no method capable of writing an intake, underwriting,
  legal, or risk-review row (upstream-output immutability);
- the Protocol exposes no execute/send/dispatch-shaped method of any kind --
  authorization RECORDS authority, nothing can carry it out; and
- no module in the credit-ops application layer contains an executor code
  path (source-text scan mirroring the risk-review adapter-boundary tests).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from creditops.application.credit_ops import analysis, assembler, evidence, processor
from creditops.application.ports.credit_ops import CreditOpsRepository

#: A method is write-capable when its NAME STARTS WITH one of these verbs.
_WRITE_VERB_PREFIXES = ("persist_", "insert_", "update_", "write_", "save_", "create_", "record_")

#: Verbs that would represent carrying an action out (or sending a request).
_EXECUTION_VERBS = ("execute", "dispatch", "send", "perform", "trigger", "launch", "run_action")


def _protocol_method_names() -> list[str]:
    return [
        name
        for name, member in vars(CreditOpsRepository).items()
        if not name.startswith("_") and inspect.isfunction(member)
    ]


def test_port_exposes_no_write_method_for_upstream_tables() -> None:
    names = _protocol_method_names()
    assert names, "expected the credit-ops port to declare methods"
    for name in names:
        lowered = name.lower()
        mentions_upstream = any(
            token in lowered
            for token in ("underwriting", "legal", "risk_review", "intake", "upstream")
        )
        if mentions_upstream:
            assert lowered.startswith("load_"), (
                f"CreditOpsRepository.{name} mentions upstream state but is "
                "not a read accessor -- the credit-ops port must be read-only "
                "for every upstream artifact"
            )
            assert not lowered.startswith(_WRITE_VERB_PREFIXES), (
                f"CreditOpsRepository.{name} looks write-capable for upstream state"
            )


def test_load_upstream_view_is_the_only_upstream_state_accessor() -> None:
    assert "load_upstream_view" in _protocol_method_names()
    upstream_named = {
        name
        for name in _protocol_method_names()
        if any(
            token in name.lower()
            for token in ("underwriting", "legal", "risk_review", "intake")
        )
    }
    assert upstream_named == set()


def test_port_has_no_execute_send_or_dispatch_method() -> None:
    # (d) the enum has no EXECUTED state AND no executor path exists: the
    # durable-state surface itself cannot express executing or sending.
    for name in _protocol_method_names():
        lowered = name.lower()
        for verb in _EXECUTION_VERBS:
            assert verb not in lowered, (
                f"CreditOpsRepository.{name} looks like an execution/send surface"
            )


def test_port_writes_are_scoped_to_package_authorization_approval_audit_only() -> None:
    allowed_write_subjects = ("package", "authorization", "approval", "audit")
    for name in _protocol_method_names():
        lowered = name.lower()
        if lowered.startswith(_WRITE_VERB_PREFIXES):
            assert any(subject in lowered for subject in allowed_write_subjects), (
                f"CreditOpsRepository.{name} is write-shaped but not scoped to "
                "credit-ops package/authorization/approval/audit state"
            )


def test_protocol_defines_only_coroutines() -> None:
    for name, member in vars(CreditOpsRepository).items():
        if name.startswith("_") or not inspect.isfunction(member):
            continue
        assert inspect.iscoroutinefunction(member), f"{name} must be an async method"


# -- source-text proof: no executor code path anywhere -------------------------


def _module_code(module: object) -> str:
    text = Path(inspect.getfile(module)).read_text(encoding="utf-8")  # type: ignore[arg-type]
    # Drop the module docstring and comment lines so the scan sees only
    # executable code (narration legitimately discusses "execution" to
    # forbid it).
    lines = text.splitlines()
    if lines and lines[0].startswith('"""'):
        end = next((i for i in range(1, len(lines)) if lines[i].strip().endswith('"""')), 0)
        lines = lines[end + 1 :]
    code_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    return "\n".join(code_lines)


def test_no_credit_ops_module_defines_an_executor_function() -> None:
    pattern = re.compile(
        r"def\s+\w*(execute|dispatch|send|perform)\w*\s*\(", re.IGNORECASE
    )
    for module in (analysis, assembler, evidence, processor):
        code = _module_code(module)
        match = pattern.search(code)
        assert match is None, (
            f"{module.__name__} defines an executor-shaped function: {match.group(0)!r}"
        )


def test_no_credit_ops_module_writes_an_executed_status() -> None:
    for module in (analysis, assembler, evidence, processor):
        code = _module_code(module)
        assert "EXECUTED" not in code, (
            f"{module.__name__} mentions an EXECUTED status in executable code"
        )


def test_authorization_recording_is_the_only_action_related_write() -> None:
    # The processor (the only writer of packages) persists and audits; the
    # ONLY action-related write anywhere on the port is record_action_
    # authorization, and it appears nowhere in the agent-side modules -- the
    # worker cannot even record an authorization, let alone execute one.
    for module in (analysis, assembler, evidence, processor):
        code = _module_code(module)
        assert "record_action_authorization" not in code, (
            f"{module.__name__} (agent-side) must never record an authorization"
        )
        assert "record_document_request_approval" not in code, (
            f"{module.__name__} (agent-side) must never record an approval"
        )
        assert "ensure_gate" not in code, (
            f"{module.__name__} (agent-side) must never write a gate"
        )
