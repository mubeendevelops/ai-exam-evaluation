resource "google_artifact_registry_repository" "images" {
  repository_id = var.name
  location      = var.region
  format        = "DOCKER"
  labels        = local.labels

  depends_on = [google_project_service.apis]
}
