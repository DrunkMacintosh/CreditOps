variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "container_image" {
  type = string
}

variable "app_env" {
  type = string
}

variable "data_class" {
  type = string
}

variable "api_cpu" {
  type = string
}

variable "api_memory" {
  type = string
}

variable "api_timeout_seconds" {
  type = number
}

variable "api_concurrency" {
  type = number
}

variable "api_min_instances" {
  type = number
}

variable "api_max_instances" {
  type = number
}

variable "worker_cpu" {
  type = string
}

variable "worker_memory" {
  type = string
}

variable "worker_timeout_seconds" {
  type = number
}

variable "api_service_account" {
  type = string
}

variable "worker_service_account" {
  type = string
}

variable "scheduler_service_account" {
  type = string
}

variable "api_invoker_members" {
  type = set(string)
}

variable "worker_runtime_ready" {
  type = bool
}

variable "api_secret_refs" {
  type = map(object({
    secret_id = string
    version   = string
  }))
}

variable "worker_secret_refs" {
  type = map(object({
    secret_id = string
    version   = string
  }))
}

resource "google_cloud_run_v2_service" "api" {
  project             = var.project_id
  location            = var.region
  name                = "creditops-api"
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = var.api_service_account
    timeout                          = "${var.api_timeout_seconds}s"
    max_instance_request_concurrency = var.api_concurrency

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    containers {
      image = var.container_image
      args  = ["api"]

      env {
        name  = "APP_ENV"
        value = var.app_env
      }

      env {
        name  = "DATA_CLASS"
        value = var.data_class
      }

      env {
        name  = "SERVICE_NAME"
        value = "creditops-api"
      }

      dynamic "env" {
        for_each = var.api_secret_refs
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = env.value.version
            }
          }
        }
      }

      resources {
        limits = {
          cpu    = var.api_cpu
          memory = var.api_memory
        }
        cpu_idle = true
      }

      ports {
        container_port = 8080
      }

      startup_probe {
        initial_delay_seconds = 1
        timeout_seconds       = 3
        period_seconds        = 3
        failure_threshold     = 10
        http_get {
          path = "/api/v1/health"
          port = 8080
        }
      }

      liveness_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 3
        period_seconds        = 10
        failure_threshold     = 3
        http_get {
          path = "/api/v1/health"
          port = 8080
        }
      }
    }
  }
}

resource "google_cloud_run_v2_job" "worker" {
  project             = var.project_id
  location            = var.region
  name                = "creditops-worker"
  deletion_protection = false

  template {
    # Cloud Run serializes tasks only within one execution. The durable worker slot
    # in Supabase is the global-one-worker invariant across API and Scheduler dispatches.
    task_count  = 1
    parallelism = 1

    template {
      service_account = var.worker_service_account
      max_retries     = 0
      timeout         = "${var.worker_timeout_seconds}s"

      containers {
        image = var.container_image
        args  = ["worker"]

        env {
          name  = "APP_ENV"
          value = var.app_env
        }

        env {
          name  = "DATA_CLASS"
          value = var.data_class
        }

        env {
          name  = "SERVICE_NAME"
          value = "creditops-worker"
        }

        dynamic "env" {
          for_each = var.worker_secret_refs
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value.secret_id
                version = env.value.version
              }
            }
          }
        }

        resources {
          limits = {
            cpu    = var.worker_cpu
            memory = var.worker_memory
          }
        }
      }
    }
  }
}

resource "google_cloud_run_v2_job_iam_member" "invoker" {
  for_each = var.worker_runtime_ready ? toset([
    var.api_service_account,
    var.scheduler_service_account,
  ]) : toset([])

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value}"
}

resource "google_cloud_run_v2_service_iam_member" "api_invoker" {
  for_each = var.api_invoker_members

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = each.value
}

output "api_uri" {
  value      = google_cloud_run_v2_service.api.uri
  depends_on = [google_cloud_run_v2_service_iam_member.api_invoker]
}

output "worker_job_name" {
  value = google_cloud_run_v2_job.worker.name
}
