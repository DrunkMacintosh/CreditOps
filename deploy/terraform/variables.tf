variable "project_id" {
  description = "Google Cloud project ID for this synthetic development environment."
  type        = string
}

variable "region" {
  description = "Approved Google Cloud region. Data residency remains an open release gate."
  type        = string
}

variable "container_image" {
  description = "Immutable API/worker image reference, preferably pinned by digest."
  type        = string

  validation {
    condition     = strcontains(var.container_image, "@sha256:")
    error_message = "container_image must be pinned by sha256 digest."
  }
}

variable "app_env" {
  description = "Application environment; this definition is restricted to synthetic development."
  type        = string
  default     = "development"

  validation {
    condition     = var.app_env == "development"
    error_message = "Only the development environment is defined by this unapproved infrastructure."
  }
}

variable "data_class" {
  description = "Authorized data class. Real banking data is prohibited."
  type        = string
  default     = "synthetic"

  validation {
    condition     = var.data_class == "synthetic"
    error_message = "Only synthetic data is authorized."
  }
}

variable "api_cpu" {
  description = "Cloud Run API CPU limit, explicitly selected for the environment."
  type        = string
}

variable "api_memory" {
  description = "Cloud Run API memory limit, explicitly selected for the environment."
  type        = string
}

variable "api_timeout_seconds" {
  description = "Cloud Run API request timeout in seconds."
  type        = number
}

variable "api_concurrency" {
  description = "Maximum concurrent requests per API instance."
  type        = number
}

variable "api_min_instances" {
  description = "Minimum API instances."
  type        = number
}

variable "api_max_instances" {
  description = "Maximum API instances."
  type        = number
}

variable "worker_cpu" {
  description = "Cloud Run worker CPU limit, explicitly selected for the environment."
  type        = string
}

variable "worker_memory" {
  description = "Cloud Run worker memory limit, explicitly selected for the environment."
  type        = string
}

variable "worker_timeout_seconds" {
  description = "Maximum duration of one worker task in seconds."
  type        = number
}

variable "worker_runtime_ready" {
  description = "Fail-closed deployment gate. Keep false until the worker has a tested durable queue sweep; current worker exits non-zero."
  type        = bool
  default     = false
}

variable "web_identity_pool_id" {
  description = "Dedicated Google Workload Identity Pool ID for the Vercel caller."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{3,30}[a-z0-9]$", var.web_identity_pool_id))
    error_message = "web_identity_pool_id must be a valid 5-32 character Google pool ID."
  }
}

variable "web_identity_provider_id" {
  description = "OIDC provider ID within the dedicated web identity pool."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{3,30}[a-z0-9]$", var.web_identity_provider_id))
    error_message = "web_identity_provider_id must be a valid 5-32 character Google provider ID."
  }
}

variable "vercel_team_slug" {
  description = "Exact Vercel team slug used to derive both OIDC issuer and audience."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", var.vercel_team_slug))
    error_message = "vercel_team_slug must contain only lower-case letters, digits, and internal hyphens."
  }
}

variable "web_oidc_subject" {
  description = "Exact approved Vercel subject: owner:<team>:project:<project>:environment:<environment>."
  type        = string

  validation {
    condition = can(regex(
      "^owner:[A-Za-z0-9_-]+:project:[A-Za-z0-9_.-]+:environment:[A-Za-z0-9_-]+$",
      var.web_oidc_subject,
    )) && startswith(var.web_oidc_subject, "owner:${var.vercel_team_slug}:")
    error_message = "web_oidc_subject must safely identify one exact deployment owned by vercel_team_slug."
  }
}

variable "operational_metrics_ready" {
  description = "Fail-closed gate. Keep false until application and worker code emit every documented operational event."
  type        = bool
  default     = false
}

variable "additional_api_invoker_members" {
  description = "Optional named Google IAM members for smoke/operations access; never public principals."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for member in var.additional_api_invoker_members :
      can(regex("^(serviceAccount|user|group):[^[:space:]]+$", member))
    ])
    error_message = "Additional API invokers must be explicit serviceAccount:, user:, or group: members."
  }
}

variable "scheduler_schedule" {
  description = "Recovery-sweep cron schedule."
  type        = string
  default     = "* * * * *"
}

variable "scheduler_time_zone" {
  description = "Recovery-sweep cron time zone."
  type        = string
  default     = "Etc/UTC"
}

variable "api_secret_refs" {
  description = "API environment names mapped to Secret Manager IDs and numeric pinned versions. No payloads."
  type = map(object({
    secret_id = string
    version   = string
  }))

  validation {
    condition = alltrue([
      for ref in values(var.api_secret_refs) : can(regex("^[1-9][0-9]*$", ref.version))
    ])
    error_message = "Every API secret reference must use a positive numeric version, never latest."
  }

  validation {
    condition = alltrue([
      for name in keys(var.api_secret_refs) :
      can(regex("^[A-Za-z_][A-Za-z0-9_]*$", name))
      && !contains([
        "APP_ENV",
        "DATA_CLASS",
        "SERVICE_NAME",
        "PORT",
        "K_SERVICE",
        "K_REVISION",
        "K_CONFIGURATION",
        "FUNCTION_TARGET",
        "FUNCTION_SIGNATURE_TYPE",
      ], upper(name))
      && !startswith(upper(name), "X_GOOGLE_")
    ])
    error_message = "API secret keys must be valid environment names and cannot override reserved Cloud Run/application variables."
  }
}

variable "worker_secret_refs" {
  description = "Worker environment names mapped to Secret Manager IDs and numeric pinned versions. No payloads."
  type = map(object({
    secret_id = string
    version   = string
  }))

  validation {
    condition = alltrue([
      for ref in values(var.worker_secret_refs) : can(regex("^[1-9][0-9]*$", ref.version))
    ])
    error_message = "Every worker secret reference must use a positive numeric version, never latest."
  }

  validation {
    condition = alltrue([
      for name in keys(var.worker_secret_refs) :
      can(regex("^[A-Za-z_][A-Za-z0-9_]*$", name))
      && !contains([
        "APP_ENV",
        "DATA_CLASS",
        "SERVICE_NAME",
        "PORT",
        "K_SERVICE",
        "K_REVISION",
        "K_CONFIGURATION",
        "FUNCTION_TARGET",
        "FUNCTION_SIGNATURE_TYPE",
      ], upper(name))
      && !startswith(upper(name), "X_GOOGLE_")
    ])
    error_message = "Worker secret keys must be valid environment names and cannot override reserved Cloud Run/application variables."
  }
}

variable "notification_channel_ids" {
  description = "Pre-existing Monitoring notification channel resource IDs."
  type        = list(string)
  default     = []
}

variable "manual_review_growth_threshold" {
  description = "Synthetic-dev manual-review event rate alert threshold per second."
  type        = number
  default     = 0.1
}

variable "dispatch_failure_threshold" {
  description = "Synthetic-dev dispatch failure event rate alert threshold per second."
  type        = number
  default     = 0
}

variable "queue_age_threshold_seconds" {
  description = "Synthetic-dev oldest eligible queue item age alert threshold."
  type        = number
  default     = 300
}

variable "provider_failure_rate_threshold" {
  description = "Synthetic-dev provider failure event rate alert threshold per second."
  type        = number
  default     = 0.01
}
