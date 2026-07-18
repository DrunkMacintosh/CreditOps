# GitHub Actions deployment configuration

This repository uses GitHub Actions to deploy the synthetic development environment. It does not authorize production banking data, official SHB policy, or production approval workflows.

## Final consolidated configuration map

Every configuration value lives in exactly one of three destinations. The rule: a value is a GitHub Actions secret only if it is a credential the *deploy pipeline* uses; runtime credentials never touch GitHub; non-secret application structure is committed to the repo. This repository is **public**, so infrastructure coordinates that reveal topology stay as variables/runtime env and are not committed.

| # | Item | Destination | Status |
| --- | --- | --- | --- |
| 1 | `VERCEL_TOKEN` | GitHub Actions **secret** (`staging`) | Required for the Vercel job |
| 2 | `SUPABASE_ACCESS_TOKEN` | GitHub Actions **secret** | Required for the Supabase job |
| 3 | `SUPABASE_DB_PASSWORD` (or `SUPABASE_DB_URL`) | GitHub Actions **secret** | Required for the Supabase job |
| 4 | `GCP_WORKLOAD_IDENTITY_PROVIDER` | GitHub Actions **secret** | Required for the Cloud Run job |
| 5 | `GCP_DEPLOYER_SERVICE_ACCOUNT` | GitHub Actions **secret** | Required for the Cloud Run job |
| 6 | `GCP_PROJECT_ID`, `GCP_REGION`, `GAR_LOCATION`, `GAR_REPOSITORY`, `CLOUD_RUN_API_SERVICE`, `CLOUD_RUN_WORKER_JOB`, `SUPABASE_PROJECT_REF`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | GitHub Actions **variables** (`staging`) | Non-secret coordinates; kept as variables (not committed) because the repo is public |
| 7 | `VERCEL_CLI_VERSION`, `WORKER_RUNTIME_READY`, `VERCEL_PRODUCTION_URL` | GitHub Actions **variables** | Pinned version / feature gate / optional smoke URL |
| 8 | `FPT_API_KEY` | **Google Secret Manager** (Cloud Run runtime) | Never in GitHub Actions. Unset until an FPT account exists |
| 9 | `FPT_{REASONING,KIE,TABLE,VISION,EMBEDDING}_ENDPOINT_URL` and `_ENDPOINT_ID` | **Cloud Run runtime env** (from Secret Manager / deploy) | Tenant-specific; not committed. Unset — OPEN QUESTION, benchmark-gated |
| 10 | `FPT_{CAP}_MODEL_ID` | **Versioned code/config** (committed catalog) | Non-secret product identifiers; hardened in code, not in repo secrets. Values benchmark-gated — commit only once chosen |
| 11 | FPT capability set, routing policy, intended endpoint↔model pairing, `route_version`, `prompt_version`, `schema_version` | **Versioned code/config** (committed) | Application structure; belongs in git for provenance |

**Non-negotiable notes on the FPT tier (items 8–11):**

- There is **no default model**. Each of the five capabilities (`reasoning`, `kie`, `table`, `vision`, `embedding`) must be pinned to its own explicit endpoint + model, or be explicitly absent. A partially-configured capability fails closed (`incomplete FPT configuration`); a silent non-FPT or unconfigured-model fallback is forbidden by the project's global constraints.
- **Model IDs are hardened into the committed catalog, not repo secrets/variables.** They are non-secret product identifiers (safe on a public repo), and pinning capability→model in versioned code gives PR review and provenance for every inference. The tenant-specific endpoint URL/ID stay in runtime env; only the API key is a secret.
- The committed `model_id` must match the model the runtime endpoint actually serves. Because model and endpoint are declared in different places, the gateway **fails closed on any endpoint↔model mismatch** rather than running a wrong model silently; the catalog records the intended pairing so drift is caught.
- The concrete endpoint IDs and model IDs are **unresolved OPEN QUESTIONS**, benchmark-gated. Commit the catalog schema and versions now; fill in real model IDs (via PR) only when benchmark-selected models exist. Do not fabricate or default placeholder values.
- The intake slice (frontend + case/upload/review) runs and fails closed **without any FPT configuration**; the FPT tier is only needed once inference stages go live.

## Required GitHub Environment

Create a protected GitHub Environment named `staging`. Put all secrets below in that environment, not in workflow YAML. Require reviewers before creating a separate `production` environment. The deploy workflow only runs from `main` after the `CI` workflow succeeds.

## Environment secrets

| Secret | Used by | Purpose and minimum scope |
| --- | --- | --- |
| `VERCEL_TOKEN` | Vercel job | A dedicated Vercel token limited to the target team/project. Rotate it regularly. |
| `SUPABASE_ACCESS_TOKEN` | Supabase job | Supabase CLI access for the one target project. Do not use a personal token when a dedicated automation identity is available. |
| `SUPABASE_DB_PASSWORD` | Supabase job | Database password required by `supabase link`/`db push`; never print it. Alternatively replace this with one `SUPABASE_DB_URL` secret and use `supabase db push --db-url`. |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Cloud Run job | Full Google provider resource name for GitHub OIDC. This is an identifier, but keep it environment-scoped with the deploy identity. |
| `GCP_DEPLOYER_SERVICE_ACCOUNT` | Cloud Run job | Dedicated deployer service account email. It must not be a user account or a JSON key. |

`VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, and `SUPABASE_PROJECT_REF` are identifiers and are configured as environment variables in the workflow. They are not credentials.

## Environment variables

Configure these as `staging` Environment variables:

| Variable | Example shape |
| --- | --- |
| `GCP_PROJECT_ID` | `synthetic-creditops-dev` |
| `GCP_REGION` | `asia-southeast1` |
| `GAR_LOCATION` | `asia-southeast1` |
| `GAR_REPOSITORY` | `creditops` |
| `CLOUD_RUN_API_SERVICE` | `creditops-api` |
| `CLOUD_RUN_WORKER_JOB` | `creditops-worker` |
| `SUPABASE_PROJECT_REF` | Supabase project ref |
| `VERCEL_ORG_ID` | Vercel team/org ID |
| `VERCEL_PROJECT_ID` | Vercel project ID |
| `VERCEL_CLI_VERSION` | An explicitly approved Vercel CLI version |
| `VERCEL_PRODUCTION_URL` | Optional HTTPS URL for a smoke check |
| `WORKER_RUNTIME_READY` | `false` until the live worker processor and recovery path are verified |

## Google IAM prerequisites

The GitHub OIDC provider must allow only this repository and the `main` ref/environment subject. The deployer service account needs only:

- Artifact Registry writer on the target repository;
- Cloud Run deploy permissions for the API service and worker Job;
- Service Account User on the Cloud Run runtime identities;
- permission to resolve the pushed Artifact Registry digest.

It does not need a service-account key. The Cloud Run runtime service accounts separately access pinned Secret Manager versions. FPT API keys, database URLs, Supabase service-role keys, and OIDC configuration stay in Google Secret Manager and are not exposed to GitHub Actions.

## Supabase migration safety

Migrations are applied before Cloud Run or Vercel deployment. The CLI uses the ordered files in `supabase/migrations/`; a failed migration stops the workflow. Review migration SQL and take the approved backup before enabling a non-synthetic environment.

## Rotation and incident response

1. Disable the affected GitHub Environment or revoke the provider/token.
2. Rotate `VERCEL_TOKEN`, Supabase access/database credentials, or the Google trust binding as applicable.
3. Review GitHub Actions logs for accidental exposure; GitHub masks configured secrets but does not redact values written to files or transformed values automatically.
4. Roll Cloud Run back to the previous immutable revision and redeploy the last known-good Vercel deployment.
5. Never add a secret to `.env`, workflow YAML, artifacts, cache keys, commit messages, or logs.
