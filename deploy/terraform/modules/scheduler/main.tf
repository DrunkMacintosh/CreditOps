variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "worker_job_name" {
  type = string
}

variable "scheduler_service_account" {
  type = string
}

variable "schedule" {
  type = string
}

variable "time_zone" {
  type = string
}

variable "worker_runtime_ready" {
  description = "Fail-closed gate; false until the worker performs a real durable queue sweep."
  type        = bool
}

resource "google_cloud_scheduler_job" "worker_recovery" {
  count = var.worker_runtime_ready ? 1 : 0

  project          = var.project_id
  region           = var.region
  name             = "creditops-worker-recovery"
  description      = "Recovery sweep; durable queue state decides whether work is eligible."
  schedule         = var.schedule
  time_zone        = var.time_zone
  attempt_deadline = "30s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri = format(
      "https://run.googleapis.com/v2/projects/%s/locations/%s/jobs/%s:run",
      var.project_id,
      var.region,
      var.worker_job_name,
    )
    body = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = var.scheduler_service_account
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

output "scheduler_job_name" {
  value = try(google_cloud_scheduler_job.worker_recovery[0].name, null)
}
