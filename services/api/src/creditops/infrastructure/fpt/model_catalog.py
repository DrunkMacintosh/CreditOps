"""Committed FPT model catalog.

Model identifiers are non-secret product identifiers (e.g. ``Qwen3-30B-A3B``),
so — unlike the tenant endpoint URL/ID and the API key — they belong in
versioned code, not in repository secrets or runtime variables. Pinning
capability -> model here gives every inference a reviewable, auditable model
lineage.

The concrete model IDs are still **benchmark-gated OPEN QUESTIONS**. This
catalog therefore ships empty: every capability fails closed until a
benchmark-selected model is added below in a reviewed pull request. Never add
a speculative or placeholder identifier; ``FPTCapabilityConfig`` also rejects
``auto``/``default``/``latest``.

Tenant-specific ``FPT_{CAP}_ENDPOINT_URL`` and ``FPT_{CAP}_ENDPOINT_ID`` are
injected from the environment at runtime; ``FPT_API_KEY`` comes from Secret
Manager. See ``docs/DEPLOYMENT_SECRETS.md`` for the full configuration map.
"""

from __future__ import annotations

from collections.abc import Mapping

from creditops.infrastructure.fpt.catalog import CapabilityName

# capability -> committed model identifier. Empty until benchmarks select a
# model per capability. Example (do NOT enable without a benchmark record):
#   "reasoning": "Qwen3-30B-A3B",
FPT_MODEL_CATALOG: Mapping[CapabilityName, str] = {}
