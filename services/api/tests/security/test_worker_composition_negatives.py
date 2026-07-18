"""``build_runtime`` (``worker/main.py``) composed against a hostile FPT
environment.

``test_worker_gate.py`` already proves the "no runtime configured -> refuse
to run" contract for ``main()``. This file covers the adjacent, distinct
composition-security property named in the master design (section 21.1,
security tests) for ``build_runtime`` itself: an environment that supplies a
*malicious-looking* FPT reasoning endpoint (plain ``http://``, or an
attacker-controlled host with a query string reminiscent of an SSRF/callback
payload) must still leave inference DISABLED, must never raise out of
``build_runtime`` into a running worker, and must never place the configured
``FPT_API_KEY`` value into a log record.

Note on the exact rejection path: ``FPTCatalog.from_configuration`` gates a
route on BOTH a well-formed HTTPS endpoint URL (``FPTCapabilityConfig``'s
``https_without_query`` validator) AND a committed benchmark-pass record
(``creditops.infrastructure.fpt.benchmark_records.FPT_BENCHMARK_RECORDS``,
which ships empty by design -- see that module's docstring). With the real,
empty registry, every route is DISABLED regardless of URL shape, so the
benchmark-pass gate is what actually fires first for the hostile inputs below.
That does not weaken this test: the point is the end-to-end, fail-closed
composition contract -- inference disabled, no raise, no leaked secret --
which holds regardless of which of the two independent gates catches the
hostile input first.
"""

from __future__ import annotations

import logging

import pytest

from creditops.application.orchestration.processors import ManualReviewProcessor
from creditops.config import Settings
from creditops.domain.orchestration import TaskType
from creditops.worker.main import WorkerRuntime, build_runtime

_WORKER_LOGGER = "creditops.worker.main"

#: A deliberately identifiable secret value: if this string shows up anywhere
#: in captured logs, the test fails -- there is no ambiguity about the source.
_API_KEY_VALUE = "fpt-live-key-DO-NOT-LEAK-9f3c7a1b"


def _settings() -> Settings:
    return Settings(
        app_env="test",
        worker_mode="agent",
        database_url="postgresql://worker:worker-pw@localhost:5432/creditops_test",
    )


def _assert_no_secret_or_hostile_url_leaked(
    caplog: pytest.LogCaptureFixture, *hostile: str
) -> None:
    for record in caplog.records:
        rendered = f"{record.getMessage()} {getattr(record, 'context', {})}"
        assert _API_KEY_VALUE not in rendered
        for needle in hostile:
            assert needle not in rendered


def test_http_scheme_endpoint_disables_inference_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    environ = {
        "FPT_REASONING_ENDPOINT_URL": "http://attacker.example.test/reasoning",
        "FPT_REASONING_ENDPOINT_ID": "reasoning-prod",
        "FPT_API_KEY": _API_KEY_VALUE,
    }

    with caplog.at_level(logging.WARNING, logger=_WORKER_LOGGER):
        runtime = build_runtime(_settings(), environ=environ)

    assert runtime is not None
    assert isinstance(runtime, WorkerRuntime)
    assert runtime.inference_enabled is False
    _assert_no_secret_or_hostile_url_leaked(caplog, "attacker.example.test")


def test_non_fpt_host_with_query_string_disables_inference_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # An https URL that is otherwise well-formed but carries a query string --
    # the shape ``https_without_query`` exists specifically to reject (and
    # would reject on its own, independent of the benchmark-pass gate).
    environ = {
        "FPT_REASONING_ENDPOINT_URL": (
            "https://169.254.169.254.attacker.example.test/v1/reasoning"
            "?callback=http://169.254.169.254/latest/meta-data"
        ),
        "FPT_REASONING_ENDPOINT_ID": "reasoning-prod",
        "FPT_API_KEY": _API_KEY_VALUE,
    }

    with caplog.at_level(logging.WARNING, logger=_WORKER_LOGGER):
        runtime = build_runtime(_settings(), environ=environ)

    assert runtime is not None
    assert runtime.inference_enabled is False
    _assert_no_secret_or_hostile_url_leaked(
        caplog, "169.254.169.254.attacker.example.test", "callback="
    )


def test_all_capabilities_hostile_still_composes_a_safe_fallback_registry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Every pinned capability pointed at a hostile config simultaneously.
    environ = {
        "FPT_REASONING_ENDPOINT_URL": "http://attacker.example.test/reasoning",
        "FPT_REASONING_ENDPOINT_ID": "reasoning-prod",
        "FPT_VISION_ENDPOINT_URL": "http://attacker.example.test/vision",
        "FPT_VISION_ENDPOINT_ID": "vision-prod",
        "FPT_EMBEDDING_ENDPOINT_URL": "http://attacker.example.test/embedding",
        "FPT_EMBEDDING_ENDPOINT_ID": "embedding-prod",
        "FPT_API_KEY": _API_KEY_VALUE,
    }

    with caplog.at_level(logging.WARNING, logger=_WORKER_LOGGER):
        runtime = build_runtime(_settings(), environ=environ)

    assert runtime is not None
    assert runtime.inference_enabled is False
    # DOCUMENT_INGESTION requires a live gateway; with none, it is not wired at
    # all and the registry's fail-closed fallback handles it -- never a crash,
    # never a partial/mocked pipeline reaching out to the hostile host.
    assert isinstance(
        runtime.registry.processor_for(TaskType.DOCUMENT_INGESTION), ManualReviewProcessor
    )
    _assert_no_secret_or_hostile_url_leaked(caplog, "attacker.example.test")


def test_hostile_environ_never_raises_a_python_exception_into_the_caller() -> None:
    # A defense-in-depth structural check: calling build_runtime with a
    # maximally hostile environment must return (None or a WorkerRuntime),
    # never propagate an exception into what would be a running worker loop.
    environ = {
        "FPT_REASONING_ENDPOINT_URL": "http://[::1]:9999/reasoning?x=<script>alert(1)</script>",
        "FPT_REASONING_ENDPOINT_ID": "auto",  # rejected identifier, too
        "FPT_API_KEY": _API_KEY_VALUE,
    }

    runtime = build_runtime(_settings(), environ=environ)

    assert runtime is None or isinstance(runtime, WorkerRuntime)
    if runtime is not None:
        assert runtime.inference_enabled is False


def test_missing_fpt_configuration_entirely_also_disables_inference_cleanly() -> None:
    # The baseline fail-closed case (no FPT_* variables at all): still no
    # raise, still disabled -- confirms the hostile-environ tests above are
    # exercising the same safe code path as "nothing configured", not some
    # different, less-tested branch.
    runtime = build_runtime(_settings(), environ={})

    assert runtime is not None
    assert runtime.inference_enabled is False
