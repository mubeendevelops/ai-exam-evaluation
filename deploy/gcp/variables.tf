variable "project_id" {
  description = "The Google Cloud project that holds everything."
  type        = string
}

variable "region" {
  description = "Region of every resource (data residency: India)."
  type        = string
  default     = "asia-south1"
}

variable "name" {
  description = "Prefix of resource names."
  type        = string
  default     = "tarn"
}

variable "domain" {
  description = "Public host name of the web app (a Google-managed certificate is issued for it). Point its DNS A record at the load balancer address in the outputs."
  type        = string
}

# --- Images (built and pushed to the Artifact Registry repository in the outputs) -----------

variable "backend_image" {
  description = "API and CLI image, built with --build-arg WITH_OCR=false (no torch, no Paddle)."
  type        = string
}

variable "worker_image" {
  description = "Worker image: the same Dockerfile with WITH_OCR=true (OCR engines)."
  type        = string
}

variable "frontend_image" {
  description = "Web app image (frontend/Dockerfile)."
  type        = string
}

# --- Databases --------------------------------------------------------------------------------

variable "db_tier_app" {
  description = "Machine type of the application database instance."
  type        = string
  default     = "db-custom-2-7680"
}

variable "db_tier_identity" {
  description = "Machine type of the identity database instance (small: credentials and tenants only)."
  type        = string
  default     = "db-custom-1-3840"
}

variable "db_availability" {
  description = "REGIONAL (a standby in another zone, automatic failover) or ZONAL."
  type        = string
  default     = "REGIONAL"
}

variable "db_disk_gb" {
  description = "Initial disk size of each database instance (it grows by itself)."
  type        = number
  default     = 20
}

variable "backup_retained_count" {
  description = "Daily backups kept per instance (point-in-time recovery covers the last 7 days)."
  type        = number
  default     = 14
}

variable "deletion_protection" {
  description = "Refuse to delete the databases, the bucket and the KMS key ring from Terraform."
  type        = bool
  default     = true
}

# --- Services ----------------------------------------------------------------------------------

variable "api_min_instances" {
  type    = number
  default = 1
}

variable "api_max_instances" {
  type    = number
  default = 10
}

variable "upload_max_bytes" {
  description = "Largest booklet upload. Cloud Run takes at most 32 MiB per request over HTTP/1 (O39, O98): larger uploads need direct-to-bucket uploads."
  type        = number
  default     = 31457280
}

variable "log_level" {
  type    = string
  default = "INFO"
}

# --- Worker -------------------------------------------------------------------------------------

variable "worker_mode" {
  description = "\"job\": a Cloud Run job started by Cloud Scheduler; \"vm\": an always-on Compute Engine VM with a GPU (use it when benchmarks show the CPU is too slow)."
  type        = string
  default     = "job"

  validation {
    condition     = contains(["job", "vm"], var.worker_mode)
    error_message = "worker_mode is \"job\" or \"vm\"."
  }
}

variable "worker_schedule" {
  description = "Cron schedule (Cloud Scheduler, UTC) that starts the worker job; each run takes the queued booklets and ends."
  type        = string
  default     = "*/5 * * * *"
}

variable "worker_cpu" {
  type    = string
  default = "4"
}

variable "worker_memory" {
  description = "TrOCR, Paddle and the embedders need about 16 GiB."
  type        = string
  default     = "16Gi"
}

variable "worker_timeout_seconds" {
  description = "Longest one worker job run may take."
  type        = number
  default     = 3600
}

variable "worker_gpu" {
  description = "An NVIDIA L4 on the Cloud Run job. In asia-south1 this is by invitation only (ask the Google account team); leave false until it is granted."
  type        = bool
  default     = false
}

variable "vm_machine_type" {
  type    = string
  default = "n1-standard-8"
}

variable "vm_gpu_type" {
  type    = string
  default = "nvidia-tesla-t4"
}

variable "vm_zone" {
  description = "Zone of the worker VM (it must offer the GPU type)."
  type        = string
  default     = "asia-south1-b"
}

variable "vm_image" {
  description = "Boot image with Docker and the NVIDIA driver (Deep Learning VM family). Check the family name is current before applying."
  type        = string
  default     = "projects/deeplearning-platform-release/global/images/family/common-cu124-ubuntu-2204"
}

# --- Optional engines (all off: student data stays in the project) ------------------------------

variable "cloud_ocr_enabled" {
  description = "Let the worker use the cloud OCR engines it has credentials for (design decision 4)."
  type        = bool
  default     = false
}

variable "docai_processor" {
  description = "Document AI OCR processor, projects/<p>/locations/<l>/processors/<id>. Create it by hand: single-region processors in asia-south1 need Google's access request. Empty = the engine is unavailable."
  type        = string
  default     = ""
}

variable "llm_scorer_enabled" {
  description = "The LLM second opinion (P19): sends answer text of the colleges switched on to the provider. Needs the tarn-groq-api-key secret (one paid-plan key) and the provider's data terms (O95)."
  type        = bool
  default     = false
}

variable "smtp_host" {
  type = string
}

variable "smtp_port" {
  type    = number
  default = 587
}

variable "smtp_user" {
  description = "SMTP login; its password is the tarn-smtp-password secret."
  type        = string
  default     = ""
}

variable "smtp_from" {
  description = "From address of verification, invitation and reset emails."
  type        = string
}
