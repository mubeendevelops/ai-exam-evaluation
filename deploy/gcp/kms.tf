# The key that wraps each tenant's data key (envelope encryption of identity records and
# backups). Rotating the key adds a version; older wrapped data keys still unwrap.
resource "google_kms_key_ring" "tarn" {
  name     = var.name
  location = var.region

  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "identity" {
  name            = "identity"
  key_ring        = google_kms_key_ring.tarn.id
  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s" # 90 days
  labels          = local.labels

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    prevent_destroy = true
  }
}
