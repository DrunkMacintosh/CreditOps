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

# capability -> committed model identifier.
#
# PROVENANCE: this stack was selected by explicit project decision (2026-07-18),
# not yet confirmed by the evaluation/benchmark harness. Re-confirm each entry
# against a synthetic-holdout benchmark before treating it as validated, and
# record the result in docs/DECISION_LOG.md.
#
# IMPORTANT: these must be the EXACT model identifiers the corresponding FPT
# endpoints expect (sent as the request ``model`` field and ``X-FPT-Model-ID``
# header). If an endpoint expects a namespaced/variant string, use that here.
#
# ``kie`` and ``table`` are intentionally unpinned: no model was selected for
# them, so those capabilities stay fail-closed (deterministic parsers still run;
# model-based KIE/table extraction is disabled until a model is chosen).
# Reranking is not one of the five catalog capabilities and remains disabled.
FPT_MODEL_CATALOG: Mapping[CapabilityName, str] = {
    "reasoning": "DeepSeek-V4-Flash",
    "vision": "Qwen2.5-VL-72B-Instruct",
    "embedding": "multilingual-e5-large",
}
