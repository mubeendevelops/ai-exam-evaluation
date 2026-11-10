# Cloud Scheduler starts the worker job. Each run takes the queued booklets one at a time and
# ends when the queue is empty (or after the time limit); a run that finds nothing costs
# seconds. Two runs at once are harmless: the queue hands each job to one worker (leases).

resource "google_cloud_scheduler_job" "worker" {
  count     = var.worker_mode == "job" ? 1 : 0
  name      = "${var.name}-worker"
  region    = var.region
  schedule  = var.worker_schedule
  time_zone = "Etc/UTC"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.worker[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.apis]
}

# The daily due-date reminder sweep (P37, D242): the same worker job with --reminders, which
# queues one notify.due job per college and then drains the queue. In "vm" mode the always-on
# worker queues the sweep itself (TARN_REMINDER_INTERVAL_HOURS). A second run on the same day
# adds no notice and sends no second email (unique per unit, kind and day).
resource "google_cloud_scheduler_job" "reminders" {
  count     = var.worker_mode == "job" ? 1 : 0
  name      = "${var.name}-reminders"
  region    = var.region
  schedule  = var.reminder_schedule
  time_zone = "Etc/UTC"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.worker[0].name}:run"
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode(jsonencode({
      overrides = {
        containerOverrides = [{ args = ["--reminders"] }]
      }
    }))

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler" {
  count    = var.worker_mode == "job" ? 1 : 0
  name     = google_cloud_run_v2_job.worker[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# Running the job with changed arguments (--reminders) needs the "with overrides" role;
# run.invoker alone may only start it as defined.
resource "google_cloud_run_v2_job_iam_member" "scheduler_overrides" {
  count    = var.worker_mode == "job" ? 1 : 0
  name     = google_cloud_run_v2_job.worker[0].name
  location = var.region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
