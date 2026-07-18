# FPT benchmark evidence

> Dữ liệu tổng hợp — mọi hồ sơ, chính sách và phản hồi đều được tạo riêng cho
> mục đích trình diễn (synthetic data only).

Every FPT capability route (`reasoning`, `kie`, `table`, `vision`, `embedding`)
ships **DISABLED**: `FPTCatalog.from_configuration` (the only path the
application, workers, and request handlers use) refuses to activate a
capability unless `services/api/src/creditops/infrastructure/fpt/benchmark_records.py`
contains a matching **PASSED** `FPTBenchmarkRecord` — same `capability`,
`model_id`, `endpoint_id`, and the current `route_version` / `prompt_version` /
`schema_version` from `catalog.py`. That registry ships empty by design. No
environment variable, secret, or runtime flag can bypass this gate.

This directory holds the **evidence artifacts** that make it possible to lift
that gate honestly: the committed synthetic Vietnamese-banking holdout
(`services/api/src/creditops/benchmarks/holdout.py`) run against a REAL live
managed FPT endpoint. Nothing on this path fabricates a result — a missing or
misconfigured endpoint prints `SKIP` and writes nothing, it never prints a
synthetic `PASS`.

## What produces evidence

- **Script:** `scripts/run_fpt_benchmark.py`
- **Catalog constructor:** `FPTCatalog.for_benchmark_evaluation` — the single,
  explicitly named path allowed to reach a live endpoint *before* a
  benchmark-pass record exists. It is never called from the composition root,
  workers, or request handlers.
- **Workflow:** `.github/workflows/fpt-benchmark.yml` (manual
  `workflow_dispatch` only, gated on the protected `staging` GitHub
  environment).

The script and the workflow only ever **produce** an evidence artifact
(`docs/benchmarks/<capability>-<model>-evidence.md`) plus, on a PASS, a
ready-to-paste `FPTBenchmarkRecord(...)` snippet and a `DECISION_LOG.md` row
template printed to the job log. Neither ever edits
`benchmark_records.py` — activation is a separate, reviewed, human commit.

## Running the job

1. Confirm the operator (a human with access to the real FPT credentials) has
   set, on this repository's protected **`staging`** GitHub environment:
   - Secret `FPT_API_KEY`
   - Variables (per capability you intend to benchmark) `FPT_REASONING_ENDPOINT_URL`
     / `FPT_REASONING_ENDPOINT_ID`, and/or
     `FPT_EMBEDDING_ENDPOINT_URL` / `FPT_EMBEDDING_ENDPOINT_ID`.
   - The model id for each capability is **not** an input here — it is the
     committed authority in
     `services/api/src/creditops/infrastructure/fpt/model_catalog.py`
     (currently `reasoning` -> `DeepSeek-V4-Flash`,
     `embedding` -> `multilingual-e5-large`). The environment cannot override
     it; the harness fails closed if `FPT_{CAP}_MODEL_ID` disagrees.
   - `kie`/`table` have no pinned model yet and cannot be benchmarked; the
     workflow's `capability` input therefore only offers `reasoning`,
     `embedding`, or `all` (meaning "every capability the script currently
     supports": reasoning + embedding).
2. Go to **Actions -> FPT benchmark -> Run workflow**, choose the capability
   (default `reasoning`), and run it against the `staging` environment (an
   approver may need to approve the environment deployment, per this repo's
   environment protection rules).
3. Read the job log:
   - `SKIP: FPT <capability> endpoint is not fully configured (...)` — the
     secret/vars above are missing or incomplete for that capability. No
     evidence is written for it.
   - `FAIL: FPT <capability> benchmark scored X/Y (< threshold ...)` — the
     endpoint responded but the holdout did not pass the PROPOSED threshold in
     `services/api/src/creditops/benchmarks/scoring.py`
     (`PROPOSED_REASONING_PASS_THRESHOLD` = 0.9,
     `PROPOSED_EMBEDDING_ORDERING_THRESHOLD` = 1.0). Per-case failures are
     printed. The route stays DISABLED — do not commit a record.
   - `FAIL: FPT <capability> benchmark could not run (<ExceptionType>)` — the
     harness could not complete a live call at all (network, auth, malformed
     response, etc.). Treat this as a blocker, not a benchmark failure to
     retry blindly: see "Adapting the client" below before re-running.
   - `PASS: FPT <capability> benchmark scored X/Y (>= threshold ...) model=... endpoint=...`
     — followed by the evidence file path, the ready-to-commit
     `FPTBenchmarkRecord(...)` snippet, and a `DECISION_LOG.md` row template.
4. Download the **`fpt-benchmark-evidence-<capability>-<run id>`** workflow
   artifact (uploaded even on failure) to get the rendered
   `docs/benchmarks/<capability>-<model>-evidence.md` file. It contains only
   non-secret identity (model id, endpoint id, versions, per-case pass/fail
   and reasons) — never the API key or endpoint URL.

## Committing a record after a real PASS

Only after a human has reviewed the evidence artifact and is satisfied it
reflects a genuine run against the real endpoint:

1. Add the printed `FPTBenchmarkRecord(...)` to
   `FPT_BENCHMARK_RECORDS` in
   `services/api/src/creditops/infrastructure/fpt/benchmark_records.py`.
2. Commit the evidence file from the downloaded artifact into
   `docs/benchmarks/<capability>-<model>-evidence.md` (the path the record's
   `evidence_ref` points at) so the evidence is versioned alongside the
   record, not only held in a time-limited workflow artifact.
3. Append the printed row template to `docs/DECISION_LOG.md`.
4. Open this as its own reviewed PR/commit, separate from unrelated changes,
   so the activation decision is auditable on its own.
5. Only after that commit lands does
   `FPTCatalog.from_configuration` — the path the deployed application,
   worker, and request handlers actually use — activate the capability
   (`_has_benchmark_pass` in `catalog.py` starts matching).

This job and this document never perform step 1-3 automatically. Activation
stays a reviewed human decision, every time.

## Known open question: the real endpoint's response shape

`FPTClient` currently assumes an OpenAI-style `{model, input} -> {output}`
contract. The real managed endpoint's response shape has not yet been
observed by this codebase — the **first live run of this job is what reveals
it**. If a run fails with a parsing/validation error (rather than a clean
`FAIL: ... scored X/Y`), do not guess-patch a fix in the workflow: capture the
raw response in the run logs, then adapt
`services/api/src/creditops/infrastructure/fpt/client.py` (and, if the
capability-vs-endpoint contract differs, `catalog.py`) in its own reviewed
change before re-running the benchmark.
