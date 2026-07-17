# Deployment runbook

## Normal release

1. Open a pull request. `CI` must pass backend tests/Ruff/mypy and frontend tests/typecheck/lint/build.
2. Merge to `main` only after review.
3. `Deploy synthetic development` runs automatically after the successful `CI` workflow.
4. It applies Supabase migrations, publishes the commit image, deploys the private Cloud Run API, and deploys the prebuilt Vercel frontend.
5. The worker Job is not changed while `WORKER_RUNTIME_READY=false`.

## Manual release

Use **Run workflow** only for a commit already on `main`. The workflow still applies migrations first and uses the protected `staging` Environment.

## Rollback

- **Supabase:** stop the workflow on migration failure; use an approved forward migration or the documented database recovery procedure. Do not edit migration history casually.
- **Cloud Run:** point the service back to the previous immutable revision/image digest using the Cloud Run console or `gcloud run services update-traffic`.
- **Vercel:** promote the last known-good deployment from the Vercel dashboard or redeploy the same commit through the workflow.
- **Worker:** leave `WORKER_RUNTIME_READY=false` unless the queue, checkpoint, and real FPT-backed processor have been live-verified.

## What this workflow does not prove

Passing GitHub Actions does not prove Supabase, Cloud Run, Vercel, FPT, data residency, restore, policy correctness, regulatory compliance, or SHB approval. Live execution requires approved synthetic environments and credentials.
