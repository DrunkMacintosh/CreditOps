# Policy retrieval runs against an approved synthetic versioned corpus

The Legal, Compliance and Collateral Agent retrieves policies and produces exact citations against a clearly labelled synthetic policy corpus, loaded through a versioned corpus configuration (corpus id, version, checksum). This satisfies the standing constraint that policy/checklist RAG stays inactive "until an approved, versioned corpus is configured" — the project owner approved the synthetic corpus as that configured corpus on 2026-07-18. Runtime remains fail-closed: with no corpus configured, the agent abstains and raises evidence gaps instead of answering policy questions.

Every synthetic policy document carries the mandatory disclaimer and must never be described as official SHB policy (AGENTS.md boundary). When an official corpus is supplied, only the corpus configuration changes; retrieval, citation, and grounding contracts stay the same.

## Considered Options

- **No retrieval / full abstention** — rejected: makes the Legal agent's "policy retrieval with exact citation" deliverable and its grounding tests untestable, while providing no additional safety over a clearly labelled synthetic corpus.
