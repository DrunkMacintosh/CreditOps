variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "secret_ids" {
  description = "Pre-existing Secret Manager container IDs. Payloads and versions are provisioned out of band."
  type        = set(string)
}

data "google_secret_manager_secret" "runtime" {
  for_each = var.secret_ids

  project   = var.project_id
  secret_id = each.value
}

output "secret_ids" {
  description = "Verified pre-existing secret container IDs."
  value       = { for name, secret in data.google_secret_manager_secret.runtime : name => secret.secret_id }
}
