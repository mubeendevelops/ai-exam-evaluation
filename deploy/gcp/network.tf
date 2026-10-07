# Private networking: Cloud SQL has no public address; Cloud Run reaches it through the VPC
# (direct VPC egress). Only private ranges go through the VPC, so calls to Google APIs and the
# LLM provider leave directly and no NAT is needed.

resource "google_compute_network" "tarn" {
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "run" {
  name                     = "${var.name}-run"
  region                   = var.region
  network                  = google_compute_network.tarn.id
  ip_cidr_range            = "10.10.0.0/24"
  private_ip_google_access = true
}

# Address range for Google-managed services (Cloud SQL private IP).
resource "google_compute_global_address" "private_services" {
  name          = "${var.name}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.tarn.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.tarn.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]

  depends_on = [google_project_service.apis]
}

# Nothing may come in from the internet except through the load balancer: the VPC has no
# ingress allow rules at all (Cloud Run egress and replies need none).
resource "google_compute_firewall" "deny_ingress" {
  name      = "${var.name}-deny-ingress"
  network   = google_compute_network.tarn.name
  direction = "INGRESS"
  priority  = 65000

  deny {
    protocol = "all"
  }

  source_ranges = ["0.0.0.0/0"]
}
