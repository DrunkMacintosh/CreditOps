variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "api_secret_ids" {
  description = "Secret IDs readable only by the API runtime identity."
  type        = set(string)
}

variable "worker_secret_ids" {
  description = "Secret IDs readable only by the worker runtime identity."
  type        = set(string)
}

variable "worker_runtime_ready" {
  description = "Only grant worker secret access after the durable worker runtime is implemented and verified."
  type        = bool
}

variable "web_identity_pool_id" {
  description = "Google Workload Identity Pool ID dedicated to the Vercel web caller."
  type        = string
}

variable "web_identity_provider_id" {
  description = "OIDC provider ID inside the dedicated web identity pool."
  type        = string
}

variable "vercel_team_slug" {
  description = "Exact Vercel team slug used to derive issuer and audience."
  type        = string
}

variable "web_oidc_subject" {
  description = "Exact OIDC subject authorized to impersonate the web invoker service account."
  type        = string
}

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "creditops-api"
  display_name = "CreditOps API runtime"
  description  = "Runtime identity for the authenticated Cloud Run API."
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "creditops-worker"
  display_name = "CreditOps worker runtime"
  description  = "Runtime identity for the private one-task Cloud Run Job."
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "creditops-scheduler"
  display_name = "CreditOps recovery scheduler"
  description  = "Identity used only to request recovery-sweep Job executions."
}

resource "google_service_account" "web_invoker" {
  project      = var.project_id
  account_id   = "creditops-web-invoker"
  display_name = "CreditOps web API invoker"
  description  = "Impersonated through WIF by the explicitly constrained web deployment identity."
}

resource "google_iam_workload_identity_pool" "web" {
  project                   = var.project_id
  workload_identity_pool_id = var.web_identity_pool_id
  display_name              = "CreditOps web callers"
  description               = "Federates only the configured web deployment principal to the API invoker identity."
}

resource "google_iam_workload_identity_pool_provider" "web" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.web.workload_identity_pool_id
  workload_identity_pool_provider_id = var.web_identity_provider_id
  display_name                       = "CreditOps web OIDC"
  description                        = "OIDC trust with explicit audience, claim mapping, condition, and principal selection."
  attribute_mapping = {
    "google.subject" = "assertion.sub"
  }
  attribute_condition = "assertion.sub == '${var.web_oidc_subject}'"

  oidc {
    issuer_uri        = "https://oidc.vercel.com/${var.vercel_team_slug}"
    allowed_audiences = ["https://vercel.com/${var.vercel_team_slug}"]
  }
}

resource "google_service_account_iam_member" "web_workload_identity_user" {
  service_account_id = google_service_account.web_invoker.name
  role               = "roles/iam.workloadIdentityUser"
  member = format(
    "principal://iam.googleapis.com/%s/subject/%s",
    google_iam_workload_identity_pool.web.name,
    var.web_oidc_subject,
  )

  depends_on = [google_iam_workload_identity_pool_provider.web]
}

resource "google_secret_manager_secret_iam_member" "api" {
  for_each = var.api_secret_ids

  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "worker" {
  for_each = var.worker_runtime_ready ? var.worker_secret_ids : toset([])

  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

output "api_service_account_email" {
  value = google_service_account.api.email
}

output "worker_service_account_email" {
  value = google_service_account.worker.email
}

output "scheduler_service_account_email" {
  value = google_service_account.scheduler.email
}

output "web_invoker_service_account_email" {
  value      = google_service_account.web_invoker.email
  depends_on = [google_service_account_iam_member.web_workload_identity_user]
}

output "web_identity_provider_name" {
  value = google_iam_workload_identity_pool_provider.web.name
}
