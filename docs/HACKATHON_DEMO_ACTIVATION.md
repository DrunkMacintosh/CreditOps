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
3. **Activate real FPT** (no fake bypass): add `FPT_REASONING_ENDPOINT_URL` +
   `FPT_REASONING_ENDPOINT_ID` as GitHub Actions secrets/vars, run the
   `fpt-benchmark` workflow, and — only if it PASSES the committed Vietnamese
   holdout — commit an `FPTBenchmarkRecord` to `benchmark_records.py` + a
   `DECISION_LOG` entry. If the run fails on the endpoint contract shape
   (`FPTClient` assumes `{model,input}→{output}`; real FPT AI Factory may be
   OpenAI-compatible), `client.py` needs a small reviewed adaptation the first
   run reveals.
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
