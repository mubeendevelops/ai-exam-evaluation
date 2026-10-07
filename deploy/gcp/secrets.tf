# Secret Manager (replicated only in var.region). Terraform creates every secret the services
# read. It fills in the ones it can generate (database URLs, the password pepper, the token
# signing key). The two it cannot are created empty: add a version by hand, see README.md:
#   tarn-smtp-password     the SMTP login's password
#   tarn-groq-api-key      one paid-plan key; only read when llm_scorer_enabled
#
# DO NOT rotate the pepper once users exist: every password hash depends on it (O13).

resource "random_password" "pepper" {
  length  = 48
  special = false
}

resource "random_password" "signing_key" {
  length  = 64
  special = false
}

locals {
  sql_ip = { for k, v in google_sql_database_instance.pg : k => v.private_ip_address }

  # sslmode=require: the instances accept TLS connections only.
  database_urls = {
    "database-url"              = "postgresql://tarn:${random_password.owner["app"].result}@${local.sql_ip["app"]}:5432/tarn?sslmode=require"
    "app-database-url"          = "postgresql://tarn_app:${random_password.app_role.result}@${local.sql_ip["app"]}:5432/tarn?sslmode=require"
    "identity-database-url"     = "postgresql://tarn:${random_password.owner["identity"].result}@${local.sql_ip["identity"]}:5432/tarn_identity?sslmode=require"
    "identity-app-database-url" = "postgresql://tarn_auth:${random_password.auth_role.result}@${local.sql_ip["identity"]}:5432/tarn_identity?sslmode=require"
  }

  generated_secrets = merge(
    local.database_urls,
    {
      "password-pepper"   = random_password.pepper.result
      "token-signing-key" = random_password.signing_key.result
    },
  )

  manual_secrets = ["smtp-password", "groq-api-key"]

  secret_names = toset(concat(keys(local.generated_secrets), local.manual_secrets))
}

resource "google_secret_manager_secret" "s" {
  for_each  = local.secret_names
  secret_id = "tarn-${each.value}"
  labels    = local.labels

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "generated" {
  for_each    = local.generated_secrets
  secret      = google_secret_manager_secret.s[each.key].id
  secret_data = each.value
}
