# Cloud Armor in front of the API (P21, D148). The API limits sign-in, recovery, password reset
# and registration per client address in each instance (tarn_api.ratelimit); this policy holds
# across instances and bans an address that keeps going.
resource "google_compute_security_policy" "api" {
  name        = "${var.name}-api"
  description = "Rate limits on the public auth and registration endpoints"

  rule {
    action      = "rate_based_ban"
    priority    = 1000
    description = "Sign-in, password reset, recovery codes, invitations, registration"

    match {
      expr {
        expression = "request.path.startsWith('/api/v1/auth/') || request.path.startsWith('/api/v1/registrations')"
      }
    }

    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"

      rate_limit_threshold {
        count        = var.armor_auth_requests_per_minute
        interval_sec = 60
      }

      ban_duration_sec = 600
      ban_threshold {
        count        = var.armor_auth_requests_per_minute * 5
        interval_sec = 300
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Everything else (the API authenticates every other route)"

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}
