variable "project_id" {
  type = string
}

variable "notification_channel_ids" {
  type = list(string)
}

variable "manual_review_growth_threshold" {
  type = number
}

variable "dispatch_failure_threshold" {
  type = number
}

variable "queue_age_threshold_seconds" {
  type = number
}

variable "provider_failure_rate_threshold" {
  type = number
}

resource "google_logging_metric" "manual_review_growth" {
  project     = var.project_id
  name        = "creditops_manual_review_created"
  description = "Count of synthetic-case tasks newly routed to manual review."
  filter      = "jsonPayload.service=\"creditops-worker\" AND jsonPayload.event=\"manual_review_created\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_logging_metric" "dispatch_failure" {
  project     = var.project_id
  name        = "creditops_worker_dispatch_failure"
  description = "Count of failed API or Scheduler worker dispatch requests."
  filter      = "jsonPayload.service=\"creditops-api\" AND jsonPayload.event=\"worker_dispatch_failed\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_logging_metric" "queue_age" {
  project         = var.project_id
  name            = "creditops_queue_age_seconds"
  description     = "Oldest eligible synthetic queue item age reported by recovery sweeps."
  filter          = "jsonPayload.service=\"creditops-worker\" AND jsonPayload.event=\"queue_age_observed\""
  value_extractor = "EXTRACT(jsonPayload.queue_age_seconds)"

  metric_descriptor {
    metric_kind = "GAUGE"
    value_type  = "DOUBLE"
    unit        = "s"
  }
}

resource "google_logging_metric" "provider_failure" {
  project     = var.project_id
  name        = "creditops_provider_failure"
  description = "Count of validated provider-call failures; alert uses its aligned event rate."
  filter      = "jsonPayload.service=\"creditops-worker\" AND jsonPayload.event=\"provider_request_failed\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "manual_review_growth" {
  project               = var.project_id
  display_name          = "CreditOps manual-review growth"
  combiner              = "OR"
  notification_channels = var.notification_channel_ids

  documentation {
    content   = "Manual-review creation is growing. Inspect evidence gaps, parser outcomes, and provider failures without logging customer content."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Manual-review creation rate exceeds the environment threshold"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.manual_review_growth.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.manual_review_growth_threshold
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_alert_policy" "dispatch_failure" {
  project               = var.project_id
  display_name          = "CreditOps worker dispatch failures"
  combiner              = "OR"
  notification_channels = var.notification_channel_ids

  documentation {
    content   = "Worker dispatches are failing. The minute recovery sweep limits stranding but does not replace investigation."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Dispatch failure rate exceeds the environment threshold"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.dispatch_failure.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.dispatch_failure_threshold
      duration        = "0s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_alert_policy" "queue_age" {
  project               = var.project_id
  display_name          = "CreditOps queue age"
  combiner              = "OR"
  notification_channels = var.notification_channel_ids

  documentation {
    content   = "The oldest eligible queue task is stale. Inspect queue leases, the durable worker slot, and worker checkpoints."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Queue age exceeds the environment threshold"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.queue_age.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.queue_age_threshold_seconds
      duration        = "60s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }

      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_alert_policy" "provider_failure_rate" {
  project               = var.project_id
  display_name          = "CreditOps provider failure rate"
  combiner              = "OR"
  notification_channels = var.notification_channel_ids

  documentation {
    content   = "Managed-provider calls are failing. There is no silent non-FPT fallback; affected work must pause or enter deterministic manual review."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Provider failure event rate exceeds the environment threshold"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.provider_failure.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.provider_failure_rate_threshold
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }
}

output "alert_policy_names" {
  value = {
    dispatch_failure      = google_monitoring_alert_policy.dispatch_failure.name
    manual_review_growth  = google_monitoring_alert_policy.manual_review_growth.name
    provider_failure_rate = google_monitoring_alert_policy.provider_failure_rate.name
    queue_age             = google_monitoring_alert_policy.queue_age.name
  }
}
