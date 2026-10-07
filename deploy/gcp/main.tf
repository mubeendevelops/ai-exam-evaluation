# Tarn AI Evaluation on Google Cloud (asia-south1). Nothing here is applied by the build: read
# README.md first. Layout: network.tf, sql.tf (two Cloud SQL instances), storage.tf, kms.tf,
# secrets.tf, iam.tf, artifact.tf, cloudrun.tf (API, web app, worker job, migration job),
# worker_vm.tf (optional GPU VM), loadbalancer.tf, armor.tf (rate limits), logging.tf, scheduler.tf.

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ]

  labels = {
    app = "tarn"
  }
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}
