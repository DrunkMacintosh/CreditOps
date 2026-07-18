"""Explicit live FPT capability smoke test.

This script never uses a fake response and never falls back to another
provider.  Without a complete configured catalog it exits with a visible
``SKIP`` so local verification cannot be mistaken for live-provider evidence.

It builds its catalog through ``FPTCatalog.for_benchmark_evaluation`` — the
explicitly named evaluation-only path — because this run is what PRODUCES the
benchmark evidence; a PASS printed here is smoke evidence for one endpoint,
not a benchmark-pass record.  Application routes stay DISABLED until a record
is committed to ``creditops.infrastructure.fpt.benchmark_records``.
"""

from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

from creditops.application.ports.model_gateway import ReasonRequest
from creditops.infrastructure.fpt.catalog import FPTCatalog
from creditops.infrastructure.fpt.client import FPTClient
from creditops.infrastructure.fpt.gateway import FPTInferenceGateway


async def main() -> int:
    try:
        catalog = FPTCatalog.for_benchmark_evaluation()
        catalog.config_for("reasoning")
    except (ValueError, KeyError) as exc:
        print(f"SKIP: FPT reasoning endpoint is not fully configured ({exc})")
        return 0
    client = FPTClient(catalog)
    try:
        gateway = FPTInferenceGateway(catalog, client, max_attempts=1)
        result = await gateway.reason(
            ReasonRequest(
                correlation_id="smoke-" + uuid4().hex,
                case_id=uuid4(),
                content="Tài liệu tổng hợp thử nghiệm tổng hợp; chỉ mô tả dữ kiện có căn cứ.",
                response_schema={"type": "object", "required": ["answer"]},
            )
        )
        print(
            "PASS: live FPT response validated "
            f"provider={result.provider} endpoint={result.endpoint_id} model={result.model_id}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - smoke output must identify live failure
        print(f"FAIL: live FPT smoke failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
