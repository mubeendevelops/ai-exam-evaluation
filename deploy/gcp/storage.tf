# Booklet pages, cleaned pages, result sheet PDFs, key files and reference diagrams, under
# college/{id}/... and global/... (never public, never listed to browsers: the API serves them).
resource "google_storage_bucket" "data" {
  name                        = "${var.project_id}-${var.name}-data"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  # No object versioning: a booklet the teacher deletes must not live on as old versions
  # (design decision 6; P21, D149). Soft delete keeps a deleted object 7 days for operators only
  # (accidental mass deletion), the same window as Cloud SQL's point-in-time recovery.
  versioning {
    enabled = false
  }

  soft_delete_policy {
    retention_duration_seconds = 604800 # 7 days
  }

  depends_on = [google_project_service.apis]
}

# OCR and embedding model weights, mounted read-only into the worker (var/models layout:
# huggingface/, paddlex/). Upload them once: gcloud storage cp -r var/models/* gs://<bucket>/
resource "google_storage_bucket" "models" {
  name                        = "${var.project_id}-${var.name}-models"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  depends_on = [google_project_service.apis]
}
