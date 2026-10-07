# Cloud Run writes a request log line per request with the full URL. Searches carry student names
# and USNs in the query string (/api/v1/students?q=, /api/v1/evaluated?student=&usn=), so request
# lines whose URL has a query are not stored (P21). The API's own access log drops the query too.
resource "google_logging_project_exclusion" "request_queries" {
  name        = "${var.name}-request-queries"
  description = "Cloud Run request logs of the API with a query string (student names, USNs)"
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${google_cloud_run_v2_service.api.name}\" AND log_name=\"projects/${var.project_id}/logs/run.googleapis.com%2Frequests\" AND httpRequest.requestUrl:\"?\""

  depends_on = [google_project_service.apis]
}
