# Two Cloud SQL for PostgreSQL 16 instances: the application database (colleges, content,
# booklets; row-level security) and, apart from it, the identity database (credentials, tenant
# registry; design decision on separate stores, P4). Private IP only, TLS required, automated
# daily backups with point-in-time recovery.
#
# The database named `tarn` / `tarn_identity` is created here; its tables, the roles `tarn_app`
# and `tarn_auth`, and the pgvector extension are created by the migration job (cloudrun.tf).
# The `tarn` user Terraform creates is a member of cloudsqlsuperuser, which is what lets the
# migrations create those roles and the extension.

locals {
  databases = {
    app = {
      instance = "${var.name}-app"
      database = "tarn"
      tier     = var.db_tier_app
    }
    identity = {
      instance = "${var.name}-identity"
      database = "tarn_identity"
      tier     = var.db_tier_identity
    }
  }
}

resource "google_sql_database_instance" "pg" {
  for_each = local.databases

  name                = each.value.instance
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection

  settings {
    edition           = "ENTERPRISE"
    tier              = each.value.tier
    availability_type = var.db_availability
    disk_type         = "PD_SSD"
    disk_size         = var.db_disk_gb
    disk_autoresize   = true

    user_labels = local.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.tarn.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "21:00" # 02:30 IST
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = var.backup_retained_count
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = 7  # Sunday
      hour         = 22 # 03:30 IST Monday
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled = true
      # Query text can hold student data: keep Query Insights to plans and timings.
      query_string_length     = 256
      record_application_tags = false
      record_client_address   = false
    }

    database_flags {
      name  = "log_checkpoints"
      value = "on"
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "db" {
  for_each = local.databases

  name     = each.value.database
  instance = google_sql_database_instance.pg[each.key].name
}

# Owner login of each database (migrations only; the services never use it).
resource "random_password" "owner" {
  for_each = local.databases
  length   = 40
  special  = false # goes into a URL
}

resource "google_sql_user" "owner" {
  for_each = local.databases

  name     = "tarn"
  instance = google_sql_database_instance.pg[each.key].name
  password = random_password.owner[each.key].result
}

# Passwords of the two application roles. The migration job gives the roles these passwords
# (`tarn db app-login`, `tarn identity app-login`).
resource "random_password" "app_role" {
  length  = 40
  special = false
}

resource "random_password" "auth_role" {
  length  = 40
  special = false
}
