output "load_balancer_ip" {
  description = "Create a DNS A record for the domain pointing here."
  value       = google_compute_global_address.lb.address
}

output "registry" {
  description = "Push the three images here (README.md)."
  value       = local.registry
}

output "manual_secrets" {
  description = "Add a version to each by hand (README.md)."
  value       = [for s in local.manual_secrets : "tarn-${s}"]
}

output "migrate_command" {
  value = "gcloud run jobs execute ${google_cloud_run_v2_job.migrate.name} --region ${var.region} --project ${var.project_id} --wait"
}

output "kms_key_ref" {
  description = "The value of TARN_KMS_KEY_REF."
  value       = "gcp-kms:${google_kms_crypto_key.identity.id}"
}

output "sql_instances" {
  value = { for k, v in google_sql_database_instance.pg : k => v.connection_name }
}
