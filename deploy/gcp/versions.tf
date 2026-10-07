terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # The state holds the generated database passwords, the password pepper and the token
  # signing key. Keep it in a private bucket (no public access, versioning on, access limited
  # to the people who deploy) created by hand before the first `terraform init`, then fill in
  # and uncomment:
  #
  # backend "gcs" {
  #   bucket = "<project>-tarn-tfstate"
  #   prefix = "tarn"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
