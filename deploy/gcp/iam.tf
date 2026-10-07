# One service account per workload, each with only what it uses.
#   api       sign-in, uploads, review: bucket, identity KMS key, its secrets
#   worker    reads booklets: bucket, models, application database secret, optional OCR/LLM
#   migrate   owner logins of both databases (the only one that sees them)
#   scheduler starts the worker job
#   web       serves static files: no access to anything
# The people who deploy (push images, run terraform) are outside this file.

resource "google_service_account" "api" {
  account_id   = "${var.name}-api"
  display_name = "Tarn API"
}

resource "google_service_account" "worker" {
  account_id   = "${var.name}-worker"
  display_name = "Tarn worker"
}

resource "google_service_account" "migrate" {
  account_id   = "${var.name}-migrate"
  display_name = "Tarn database migrations"
}

resource "google_service_account" "scheduler" {
  account_id   = "${var.name}-scheduler"
  display_name = "Tarn scheduler (starts the worker job)"
}

resource "google_service_account" "web" {
  account_id   = "${var.name}-web"
  display_name = "Tarn web app (static files)"
}

locals {
  # Which service account reads which secret (names as in secrets.tf).
  secret_readers = {
    api = [
      "app-database-url", "identity-app-database-url", "password-pepper",
      "password-pepper-previous", "token-signing-key", "smtp-password",
    ]
    worker  = ["app-database-url", "groq-api-key"]
    migrate = ["database-url", "app-database-url", "identity-database-url", "identity-app-database-url"]
  }

  service_accounts = {
    api     = google_service_account.api.email
    worker  = google_service_account.worker.email
    migrate = google_service_account.migrate.email
  }

  secret_grants = merge([
    for who, names in local.secret_readers : {
      for name in names : "${who}/${name}" => { who = who, name = name }
    }
  ]...)
}

resource "google_secret_manager_secret_iam_member" "read" {
  for_each  = local.secret_grants
  secret_id = google_secret_manager_secret.s[each.value.name].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.service_accounts[each.value.who]}"
}

# Booklet files: the API and the worker read and write; nobody else touches the bucket.
resource "google_storage_bucket_iam_member" "data" {
  for_each = { api = google_service_account.api.email, worker = google_service_account.worker.email }
  bucket   = google_storage_bucket.data.name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${each.value}"
}

resource "google_storage_bucket_iam_member" "models" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# Wrap and unwrap tenant data keys: the API (sign-in, registration) only.
resource "google_kms_crypto_key_iam_member" "identity" {
  crypto_key_id = google_kms_crypto_key.identity.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.api.email}"
}

# Document AI, only when the cloud OCR engines are switched on.
resource "google_project_iam_member" "worker_documentai" {
  count   = var.cloud_ocr_enabled && var.docai_processor != "" ? 1 : 0
  project = var.project_id
  role    = "roles/documentai.apiUser"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "logging" {
  for_each = {
    api     = google_service_account.api.email
    worker  = google_service_account.worker.email
    migrate = google_service_account.migrate.email
    web     = google_service_account.web.email
  }
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${each.value}"
}

# Pull images (the Cloud Run service agent does it with the service account's rights).
resource "google_artifact_registry_repository_iam_member" "pull" {
  for_each = {
    api     = google_service_account.api.email
    worker  = google_service_account.worker.email
    migrate = google_service_account.migrate.email
    web     = google_service_account.web.email
  }
  repository = google_artifact_registry_repository.images.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${each.value}"
}
