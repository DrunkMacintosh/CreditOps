# Hackathon live-demo activation runbook

Public anonymous demo using **synthetic data only**. This is a public hackathon
deployment on live managed infrastructure — **not** a production banking system,
not SHB-approved, no regulatory compliance claim. AI never approves/rejects
credit and never bypasses a human gate. Supabase stays private: RLS on, no `anon`
table/bucket grants; the browser only reaches the DB/Storage through the
backend-created upload intent and the BFF proxy.

## What this branch ships (code-complete, CI-green)

- **Anonymous demo session** — `POST /api/v1/demo-sessions` mints a fresh
  synthetic actor (random UUID) + a self-assigned synthetic case + a short-TTL
  RS256 demo JWT. RLS (`SET LOCAL ROLE creditops_api` + `set_config` on the
  actor id) isolates each session from every other. Not behind `require_actor`;
  platform-protected (Cloud Run invoker) + in-memory rate limit; returns 404 when
  demo mode is off.
- **BFF `/api/demo-session`** bootstrap sets `__Host-creditops-workforce`
  (HttpOnly/Secure/SameSite=Lax) + CSRF cookie. **Header fix**: the Google Cloud
  Run OIDC id token now rides in `Authorization` (so the private
  `--no-allow-unauthenticated` invoker check passes) and the app session JWT in
  `X-CreditOps-Authorization` (read first by `require_actor`, falling back to
  `Authorization` for backward compatibility).
- **Landing "Trải nghiệm demo" CTA** → bootstrap → into the working app. Upload
  status now polls (QUEUED→RUNNING→COMPLETED/FAILED); FAILED is shown honestly,
  no fabricated success.
- **Deep `/api/v1/ready`** probes DB / queue / storage / FPT capability state
  (non-secret) with an overall boolean; `/health` stays cheap.
- **P0-A** worker task-lease renewal (background heartbeat renewing task lease +
  worker slot + queue visibility; cancel-and-abandon on lease loss). **P0-B**
  live typed EvidenceGraph edge writer at fact confirmation + an append-only
  immutability trigger on `evidence_edges`.
- **`.github/workflows/fpt-benchmark.yml`** — manual job to honestly mint a real
  FPT benchmark-pass record against the live endpoint (no fake bypass).

## Steps that require cloud access (cannot be run from the build environment)

The build environment has no `gcloud`/`vercel`/`supabase` CLI; the deploy runs in
GitHub Actions. These need an operator with the cloud credentials:

1. **Verify infra is provisioned** (run `terraform apply` if not): GCP project,
   Cloud Run API + worker Job, Supabase project, Vercel project. (GitHub
   secrets/vars already exist: `FPT_API_KEY`, `GCP_*`, `SUPABASE_*`, `VERCEL_*`.)
2. **Enable demo mode on the Cloud Run API** (Secret Manager / service env):
   - `DEMO_SESSION_ENABLED=true`
   - `DEMO_JWT_PRIVATE_KEY` = a fresh RSA≥2048 PEM
     (`openssl genpkey -algorithm RSA -pkeyopts rsa_keygen_bits:2048`). Never
     commit it.
   - optional: `DEMO_JWT_ISSUER`, `DEMO_JWT_AUDIENCE`, `DEMO_JWT_KID`,
     `DEMO_SESSION_TTL_SECONDS`.
   - Existing runtime secrets (`DATABASE_URL`, `SUPABASE_URL`,
     `SUPABASE_SERVICE_ROLE_KEY`) stay as-is. With demo enabled, external
     `OIDC_*` is not required. Do **not** also configure external `OIDC_*` at the
     same time (the OIDC verifier would reject demo tokens — a known footgun).
3. **Activate real FPT** (no fake bypass). FPT AI Factory's managed endpoints
   are **OpenAI-compatible**: the reasoning endpoint is a
   `POST <base>/v1/chat/completions` URL and the embedding endpoint is a
   `POST <base>/v1/embeddings` URL — set `FPT_REASONING_ENDPOINT_URL` /
   `FPT_EMBEDDING_ENDPOINT_URL` to those full URLs (including the `/v1/...`
   path), not just the provider's base host. Only `FPT_API_KEY` is a secret;
   the endpoint URL/ID are non-secret GitHub Actions **variables** — both live
   on the protected `staging` GitHub environment read by
   `.github/workflows/fpt-benchmark.yml`:

   ```bash
   gh secret set   FPT_API_KEY               --env staging
   gh variable set FPT_REASONING_ENDPOINT_URL --env staging --body "https://<fpt-base>/v1/chat/completions"
   gh variable set FPT_REASONING_ENDPOINT_ID  --env staging --body "<reasoning-endpoint-id>"
   gh variable set FPT_EMBEDDING_ENDPOINT_URL --env staging --body "https://<fpt-base>/v1/embeddings"
   gh variable set FPT_EMBEDDING_ENDPOINT_ID  --env staging --body "<embedding-endpoint-id>"
   ```

   Then the **run -> evidence -> commit-record -> activate** flow (full detail
   in [`docs/benchmarks/README.md`](docs/benchmarks/README.md)):

   1. **Run** — Actions -> `FPT benchmark` -> Run workflow, pick `capability`
      (`reasoning` / `embedding` / `all`), approve the `staging` environment
      if prompted. This calls `scripts/run_fpt_benchmark.py` via
      `FPTCatalog.for_benchmark_evaluation` — the only path allowed to reach a
      live endpoint before a pass record exists.
   2. **Evidence** — on `PASS` the job writes
      `docs/benchmarks/<capability>-<model>-evidence.md` (no secrets); download
      the `fpt-benchmark-evidence-<capability>-<run id>` artifact to get it.
      On `FAIL`/`SKIP`, no evidence indicates a pass and no record follows —
      do not hand-write one.
   3. **Commit-record** (mechanical, reviewed) — commit the downloaded
      evidence file into `docs/benchmarks/`, then run:

      ```bash
      uv run python3 scripts/build_fpt_benchmark_record.py \
        docs/benchmarks/<capability>-<model>-evidence.md \
        --capability <capability> --model-id <model-id> --endpoint-id <endpoint-id> \
        --recorded-on <YYYY-MM-DD>
      ```

      It re-derives the exact `FPTBenchmarkRecord(...)` literal from the
      committed evidence (`route_version`/`prompt_version`/`schema_version`
      always come from `catalog.py`, never the file) and **refuses** (non-zero
      exit, nothing on stdout) unless that evidence's own `Verdict:` line reads
      `PASS` and its identity matches what you asked for. Paste its stdout into
      `FPT_BENCHMARK_RECORDS` in
      `services/api/src/creditops/infrastructure/fpt/benchmark_records.py`,
      append a `DECISION_LOG.md` row, and land it as its own reviewed PR.
   4. **Activate** — once that PR merges, `FPTCatalog.from_configuration` (the
      path the deployed API/worker actually use) activates the capability; no
      other step is needed.

   If a run instead fails on the endpoint *contract shape* — `FPTClient`
   currently sends an internal `{model, input} -> {output}` JSON body, not the
   OpenAI `chat/completions`/`embeddings` request/response shape — that is
   expected until `client.py` is adapted to speak the OpenAI-compatible
   protocol in its own reviewed change; capture the raw response in the job
   log rather than guess-patching (see the "Known open question" section of
   `docs/benchmarks/README.md`).
4. **Flip `WORKER_RUNTIME_READY=true`** only after the worker path is
   live-verified.
5. **Merge this PR to `main`** → CI → `Deploy synthetic development` runs
   (Supabase migrate → Cloud Run API → worker → Vercel).

## Live E2E smoke (after deploy)

No-cookie landing → "Trải nghiệm demo" → app → upload a synthetic PDF →
QUEUED→RUNNING→COMPLETED with evidence/provenance → force a failure → UI shows
FAILED (no fallback) → open a second session, confirm it cannot see the first
session's case. Capture (no secrets): public URL, deployed SHA, Cloud Run
revision, worker execution id, task id, correlation id, FPT route/model id,
timestamps, result.

## Known follow-ups (from the independent P0 audit / review)

- **P0-B** currently materializes the confirmation-side hops
  (CONFIRMED_FACT → CANDIDATE_FACT/PAGE_REGION/DOCUMENT_VERSION). Analytical-side
  edges (calculation / finding / challenge / memo) are not yet written — a
  follow-up to complete the full lineage chain.
- `evidence_edges` has no DB-level FK/CHECK on endpoints/types (allowlist is
  enforced at the application boundary); adding composite FKs + a type CHECK is a
  defense-in-depth follow-up.
- Cross-session isolation is enforced by Postgres RLS (guarded by the pgTAP suite
  in CI); the local API-layer tests use fakes and cannot exercise real RLS.
