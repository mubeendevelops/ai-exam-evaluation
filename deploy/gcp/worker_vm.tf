# Optional: the worker on an always-on Compute Engine VM with a GPU (worker_mode = "vm"), for
# when the benchmarks show the CPU is too slow. It runs the same worker image as a service
# (`python -m tarn_worker`: polls the queue, health on :8001, no job time limit). It has no
# external address; it reaches Google APIs through Private Google Access and the VPC reaches
# Cloud SQL by private IP. The image is the Deep Learning VM family (Docker + NVIDIA driver).

resource "google_compute_instance" "worker" {
  count        = var.worker_mode == "vm" ? 1 : 0
  name         = "${var.name}-worker"
  zone         = var.vm_zone
  machine_type = var.vm_machine_type
  labels       = local.labels

  boot_disk {
    initialize_params {
      image = var.vm_image
      size  = 150
      type  = "pd-ssd"
    }
  }

  guest_accelerator {
    type  = var.vm_gpu_type
    count = 1
  }

  scheduling {
    on_host_maintenance = "TERMINATE"
    automatic_restart   = true
  }

  network_interface {
    subnetwork = google_compute_subnetwork.run.id
    # No access_config: no external IP.
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  service_account {
    email  = google_service_account.worker.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -euo pipefail
    gcloud auth configure-docker ${var.region}-docker.pkg.dev --quiet
    mkdir -p /opt/tarn/models
    gcloud storage rsync -r gs://${google_storage_bucket.models.name} /opt/tarn/models
    DB_URL="$(gcloud secrets versions access latest --secret=tarn-app-database-url)"
    docker rm -f tarn-worker 2>/dev/null || true
    docker run -d --name tarn-worker --restart=always --gpus all \
      -v /opt/tarn/models:/models \
      -e TARN_ENV=production -e TARN_LOG_LEVEL=${var.log_level} -e TARN_DEVICE=cuda \
      -e TARN_BLOB_BACKEND=gcs -e TARN_BLOB_BUCKET=${google_storage_bucket.data.name} \
      -e TARN_GCP_PROJECT=${var.project_id} -e TARN_SECRETS_BACKEND=gcp \
      -e TARN_KMS_KEY_REF=gcp-kms:${google_kms_crypto_key.identity.id} \
      -e TARN_MAILER=smtp -e TARN_SMTP_HOST=${var.smtp_host} -e TARN_SMTP_FROM=${var.smtp_from} \
      -e TARN_MODEL_DIR=/models -e TARN_WORKER_HEALTH_HOST=0.0.0.0 \
      -e TARN_IDENTITY_BACKUP_INTERVAL_HOURS=0 \
      -e TARN_CLOUD_OCR_ENABLED=${var.cloud_ocr_enabled} -e TARN_DOCAI_PROCESSOR=${var.docai_processor} \
      -e TARN_LLM_SCORER_ENABLED=${var.llm_scorer_enabled} \
      -e TARN_APP_DATABASE_URL="$DB_URL" \
      ${var.worker_image} python -m tarn_worker
  EOT

  depends_on = [
    google_secret_manager_secret_version.generated,
    google_secret_manager_secret_iam_member.read,
    google_storage_bucket_iam_member.models,
  ]
}
