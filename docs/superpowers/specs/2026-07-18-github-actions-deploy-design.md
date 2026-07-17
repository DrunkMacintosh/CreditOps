# GitHub Actions Deployment Design

## Goal

Provide a controlled GitHub Actions delivery path for the Vietnamese Vercel frontend, Supabase migrations, and Google Cloud Run API/worker while keeping deployment credentials out of source control and avoiding long-lived Google service-account keys.

## Scope and boundaries

- Pull requests run validation only; they do not mutate cloud environments.
- A push to `main` deploys the synthetic development environment after validation.
- Production deployment is not enabled by this change; only a protected GitHub Environment with an explicit approval gate may enable it in a separate rollout.
- Supabase migrations run before application deployment and use the repository's ordered migrations.
- Cloud Run authentication uses GitHub Actions OIDC → Google Workload Identity Federation.
- Vercel uses its scoped deployment token and project identifiers.
- FPT credentials remain in Google Secret Manager and are referenced by Cloud Run; they are never copied into GitHub secrets or workflow logs.
- The existing Terraform safety gate remains authoritative: `worker_runtime_ready=false` unless a real worker processor and live queue recovery have been verified.
- Real banking data, official SHB policy, and production claims remain out of scope.

## Workflow architecture

1. `ci.yml` runs on pull requests and pushes: frontend tests/typecheck/lint/build, backend tests/ruff/mypy, and workflow/config checks.
2. `deploy.yml` runs only after CI succeeds on `main`:
   - authenticate to Supabase and apply migrations;
   - authenticate to Google Cloud through WIF;
   - build and push the API/worker image to Artifact Registry with a commit SHA tag;
   - deploy the Cloud Run API service using the immutable image;
   - update the Cloud Run worker Job only when the configured gate permits it;
   - deploy the Vercel frontend using the exact project/team IDs.
3. All deploy jobs use least-privilege environment-scoped secrets and `concurrency` to prevent overlapping deploys.
4. Every external command is fail-closed, uses pinned versions where practical, and avoids echoing credentials.

## Required GitHub configuration

### Repository secrets

- `VERCEL_TOKEN`
- `SUPABASE_ACCESS_TOKEN`
- `SUPABASE_DB_PASSWORD` (or replace with one `SUPABASE_DB_URL` secret)

### Repository or environment variables

- `GCP_PROJECT_ID`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_DEPLOYER_SERVICE_ACCOUNT`
- `GCP_REGION`
- `GAR_LOCATION`
- `GAR_REPOSITORY`
- `CLOUD_RUN_API_SERVICE`
- `CLOUD_RUN_WORKER_JOB`
- `SUPABASE_PROJECT_REF`
- `VERCEL_ORG_ID`
- `VERCEL_PROJECT_ID`
- `VERCEL_ENVIRONMENT`

### Google Secret Manager (not GitHub)

`DATABASE_URL`, OIDC settings, Supabase service-role key, FPT endpoint/API credentials, and any future production secrets remain pinned Secret Manager versions referenced by Terraform. GitHub Actions only deploys references and never reads their payloads.

## Failure and rollback behavior

- CI failure prevents all deploy jobs.
- Migration failure prevents Cloud Run and Vercel deployment.
- Cloud Run deployment uses the immutable commit image; a failed revision does not silently switch traffic.
- Vercel deployment failure leaves the previous deployment intact.
- `worker_runtime_ready` remains false by default and the worker entrypoint refuses an uninjected runtime.
- Workflow logs must contain no tokens, signed URLs, database URLs, or model credentials.

## Verification

- YAML parses and Actions expressions are reviewed statically.
- Existing backend and frontend test suites remain required gates.
- Terraform formatting/validation is attempted when the Terraform CLI is available; absence of the CLI is reported rather than masked.
- Live provider execution is not claimed until credentials and approved synthetic environments are supplied.
