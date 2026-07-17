output "api_url" {
  description = "Cloud Run API URI. Invoker access remains IAM-gated."
  value       = module.cloud_run.api_uri
}

output "worker_job_name" {
  description = "Cloud Run worker Job name."
  value       = module.cloud_run.worker_job_name
}

output "service_account_emails" {
  description = "Separate runtime identities; these are identifiers, not credentials."
  value = {
    api         = module.iam.api_service_account_email
    worker      = module.iam.worker_service_account_email
    scheduler   = module.iam.scheduler_service_account_email
    web_invoker = module.iam.web_invoker_service_account_email
  }
}

output "web_identity_provider_name" {
  description = "Full WIF provider resource name used by the server-side web caller credential configuration."
  value       = module.iam.web_identity_provider_name
}

output "secret_ids" {
  description = "Pre-existing Secret Manager containers verified without reading payloads."
  value       = module.secrets.secret_ids
}

output "alert_policy_names" {
  description = "Operational alert-policy names, empty while telemetry producers remain gated."
  value       = try(module.monitoring[0].alert_policy_names, {})
}

module "secrets" {
  source = "./modules/secrets"

  project_id = var.project_id
  secret_ids = toset(concat(
    [for ref in values(var.api_secret_refs) : ref.secret_id],
    [for ref in values(var.worker_secret_refs) : ref.secret_id],
  ))

  depends_on = [google_project_service.required]
}

module "iam" {
  source = "./modules/iam"

  project_id                   = var.project_id
  api_secret_ids               = toset([for ref in values(var.api_secret_refs) : ref.secret_id])
  worker_secret_ids            = toset([for ref in values(var.worker_secret_refs) : ref.secret_id])
  worker_runtime_ready        = var.worker_runtime_ready
  web_identity_pool_id         = var.web_identity_pool_id
  web_identity_provider_id     = var.web_identity_provider_id
  vercel_team_slug             = var.vercel_team_slug
  web_oidc_subject             = var.web_oidc_subject

  depends_on = [module.secrets, google_project_service.required]
}

module "cloud_run" {
  source = "./modules/cloud_run"

  project_id                = var.project_id
  region                    = var.region
  container_image           = var.container_image
  app_env                   = var.app_env
  data_class                = var.data_class
  api_cpu                   = var.api_cpu
  api_memory                = var.api_memory
  api_timeout_seconds       = var.api_timeout_seconds
  api_concurrency           = var.api_concurrency
  api_min_instances         = var.api_min_instances
  api_max_instances         = var.api_max_instances
  worker_cpu                = var.worker_cpu
  worker_memory             = var.worker_memory
  worker_timeout_seconds    = var.worker_timeout_seconds
  worker_runtime_ready      = var.worker_runtime_ready
  api_service_account       = module.iam.api_service_account_email
  worker_service_account    = module.iam.worker_service_account_email
  scheduler_service_account = module.iam.scheduler_service_account_email
  api_secret_refs           = var.api_secret_refs
  worker_secret_refs        = var.worker_secret_refs
  api_invoker_members = setunion(
    var.additional_api_invoker_members,
    toset(["serviceAccount:${module.iam.web_invoker_service_account_email}"]),
  )

  depends_on = [module.iam]
}

module "scheduler" {
  source = "./modules/scheduler"

  project_id                = var.project_id
  region                    = var.region
  worker_job_name           = module.cloud_run.worker_job_name
  scheduler_service_account = module.iam.scheduler_service_account_email
  schedule                  = var.scheduler_schedule
  time_zone                 = var.scheduler_time_zone
  worker_runtime_ready      = var.worker_runtime_ready

  depends_on = [module.cloud_run]
}

module "monitoring" {
  count  = var.operational_metrics_ready ? 1 : 0
  source = "./modules/monitoring"

  project_id                      = var.project_id
  notification_channel_ids        = var.notification_channel_ids
  manual_review_growth_threshold  = var.manual_review_growth_threshold
  dispatch_failure_threshold      = var.dispatch_failure_threshold
  queue_age_threshold_seconds     = var.queue_age_threshold_seconds
  provider_failure_rate_threshold = var.provider_failure_rate_threshold

  depends_on = [google_project_service.required]
}
