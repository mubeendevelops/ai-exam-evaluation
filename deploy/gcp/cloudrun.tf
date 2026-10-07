# Cloud Run: the API and the web app (services behind the load balancer), the worker (a job
# started on a schedule, or a VM: worker_vm.tf) and the migration job (run it by hand after
# every deploy that changes the schema: README.md).

locals {
  registry = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"

  secret_env = {
    app_database_url          = "TARN_APP_DATABASE_URL"
    identity_app_database_url = "TARN_IDENTITY_APP_DATABASE_URL"
  }

  # Variables every process of the backend image reads.
  common_env = {
    TARN_ENV             = "production"
    TARN_LOG_LEVEL       = var.log_level
    TARN_DEVICE          = "auto"
    TARN_BLOB_BACKEND    = "gcs"
    TARN_BLOB_BUCKET     = google_storage_bucket.data.name
    TARN_GCP_PROJECT     = var.project_id
    TARN_GCP_REGION      = var.region
    TARN_SECRETS_BACKEND = "gcp"
    TARN_KMS_KEY_REF     = "gcp-kms:${google_kms_crypto_key.identity.id}"
    TARN_PUBLIC_URL      = "https://${var.domain}"
    TARN_MAILER          = "smtp"
    TARN_SMTP_HOST       = var.smtp_host
    TARN_SMTP_PORT       = tostring(var.smtp_port)
    TARN_SMTP_USER       = var.smtp_user
    TARN_SMTP_FROM       = var.smtp_from
  }
}

resource "google_cloud_run_v2_service" "api" {
  name                = "${var.name}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = var.deletion_protection
  labels              = local.labels

  template {
    service_account                  = google_service_account.api.email
    max_instance_request_concurrency = 8 # an upload is held in memory (O39)
    timeout                          = "300s"

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.tarn.id
        subnetwork = google_compute_subnetwork.run.id
      }
    }

    containers {
      image = var.backend_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
      }

      startup_probe {
        http_get {
          path = "/api/v1/health"
        }
        period_seconds    = 5
        failure_threshold = 12
      }

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name  = "TARN_UPLOAD_MAX_BYTES"
        value = tostring(var.upload_max_bytes)
      }

      env {
        name = "TARN_APP_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.s["app-database-url"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "TARN_IDENTITY_APP_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.s["identity-app-database-url"].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_version.generated,
    google_secret_manager_secret_iam_member.read,
    google_storage_bucket_iam_member.data,
    google_kms_crypto_key_iam_member.identity,
    google_artifact_registry_repository_iam_member.pull,
  ]
}

resource "google_cloud_run_v2_service" "web" {
  name                = "${var.name}-web"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = var.deletion_protection
  labels              = local.labels

  template {
    service_account = google_service_account.web.email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = var.frontend_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
      }
    }
  }

  depends_on = [google_artifact_registry_repository_iam_member.pull]
}

# --- Worker: one run takes the queued booklets, one at a time, and ends -----------------------

resource "google_cloud_run_v2_job" "worker" {
  count               = var.worker_mode == "job" ? 1 : 0
  name                = "${var.name}-worker"
  location            = var.region
  deletion_protection = var.deletion_protection
  labels              = local.labels

  template {
    task_count  = 1
    parallelism = 1 # booklets are processed one at a time (design decision 12)

    template {
      service_account = google_service_account.worker.email
      max_retries     = 1
      timeout         = "${var.worker_timeout_seconds}s"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.tarn.id
          subnetwork = google_compute_subnetwork.run.id
        }
      }

      dynamic "node_selector" {
        for_each = var.worker_gpu ? [1] : []
        content {
          accelerator = "nvidia-l4"
        }
      }

      gpu_zonal_redundancy_disabled = var.worker_gpu ? true : null

      volumes {
        name = "models"
        gcs {
          bucket    = google_storage_bucket.models.name
          read_only = true
        }
      }

      containers {
        image   = var.worker_image
        command = ["python", "-m", "tarn_worker.job"]

        resources {
          limits = merge(
            { cpu = var.worker_cpu, memory = var.worker_memory },
            var.worker_gpu ? { "nvidia.com/gpu" = "1" } : {},
          )
        }

        volume_mounts {
          name       = "models"
          mount_path = "/models"
        }

        dynamic "env" {
          for_each = merge(local.common_env, {
            TARN_MODEL_DIR              = "/models"
            TARN_CLOUD_OCR_ENABLED      = tostring(var.cloud_ocr_enabled)
            TARN_DOCAI_PROCESSOR        = var.docai_processor
            TARN_LLM_SCORER_ENABLED     = tostring(var.llm_scorer_enabled)
            TARN_WORKER_HEALTH_PORT     = "0"
            TARN_WORKER_JOB_MAX_SECONDS = tostring(var.worker_timeout_seconds - 300)
          })
          content {
            name  = env.key
            value = env.value
          }
        }

        env {
          name = "TARN_APP_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.s["app-database-url"].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_version.generated,
    google_secret_manager_secret_iam_member.read,
    google_storage_bucket_iam_member.data,
    google_storage_bucket_iam_member.models,
    google_artifact_registry_repository_iam_member.pull,
  ]
}

# --- Migrations: run by hand ---------------------------------------------------------------------
#   gcloud run jobs execute tarn-migrate --region asia-south1 --wait

resource "google_cloud_run_v2_job" "migrate" {
  name                = "${var.name}-migrate"
  location            = var.region
  deletion_protection = var.deletion_protection
  labels              = local.labels

  template {
    template {
      service_account = google_service_account.migrate.email
      max_retries     = 0
      timeout         = "900s"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.tarn.id
          subnetwork = google_compute_subnetwork.run.id
        }
      }

      containers {
        image   = var.backend_image
        command = ["sh", "-c"]
        args = [
          "tarn db upgrade && tarn db app-login && tarn identity upgrade && tarn identity app-login",
        ]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "TARN_ENV"
          value = "production"
        }

        dynamic "env" {
          for_each = {
            TARN_DATABASE_URL              = "database-url"
            TARN_APP_DATABASE_URL          = "app-database-url"
            TARN_IDENTITY_DATABASE_URL     = "identity-database-url"
            TARN_IDENTITY_APP_DATABASE_URL = "identity-app-database-url"
          }
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.s[env.value].secret_id
                version = "latest"
              }
            }
          }
        }

        # Production settings are validated even by the CLI: the same values the services use.
        dynamic "env" {
          for_each = local.common_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_version.generated,
    google_secret_manager_secret_iam_member.read,
    google_artifact_registry_repository_iam_member.pull,
  ]
}
