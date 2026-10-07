# Tarn on Google Cloud (asia-south1)

Terraform for the production stack. **Nothing here has been applied**: it was written and
checked with `terraform validate` before any Google Cloud account existed. Read the
"Not verified" list before the first `apply`.

```text
 browser ──HTTPS──▶ global load balancer (one IP, managed certificate)
                      ├─ /api/*  ▶ Cloud Run service  tarn-api   (tarn_app, tarn_auth roles)
                      └─ /*      ▶ Cloud Run service  tarn-web   (nginx, static files)
 Cloud Scheduler ──▶ Cloud Run job tarn-worker  (queue, OCR, scoring; or a GPU VM)
 gcloud (by hand) ─▶ Cloud Run job tarn-migrate (schema, roles, pgvector)

 tarn-api / tarn-worker ──VPC, private IP, TLS──▶ Cloud SQL tarn-app       (PostgreSQL 16)
 tarn-api               ──VPC, private IP, TLS──▶ Cloud SQL tarn-identity  (separate instance)
 both ──▶ Cloud Storage <project>-tarn-data    Cloud KMS key ring tarn    Secret Manager
```

| File | What |
| --- | --- |
| `network.tf` | VPC, subnet for Cloud Run direct VPC egress, private services range for Cloud SQL, deny-all ingress firewall |
| `sql.tf` | Two Cloud SQL PG16 instances: private IP only, TLS required, daily backups (14 kept) and point-in-time recovery (7 days), regional failover |
| `storage.tf`, `kms.tf` | Data bucket (versioned, never public), models bucket, KMS key for tenant data keys (rotated every 90 days) |
| `secrets.tf` | Every secret the services read; database URLs, pepper and signing key are generated |
| `iam.tf` | One service account per workload, access granted per secret, per bucket, per key |
| `cloudrun.tf` | API and web services (reachable only through the load balancer), worker job, migration job |
| `worker_vm.tf` | Optional always-on GPU VM instead of the worker job (`worker_mode = "vm"`) |
| `loadbalancer.tf`, `scheduler.tf` | Public entry point (one origin: the refresh cookie needs it) and the worker schedule |

## Before the first apply

1. A Google Cloud project with billing, and `gcloud auth application-default login` as someone
   who may create these resources (Owner, or the roles for each API in `main.tf`).
2. A private bucket for the Terraform state (the state holds generated passwords), then
   uncomment the `backend "gcs"` block in `versions.tf`.
3. `cp terraform.tfvars.example terraform.tfvars` and fill it in.
4. `terraform init && terraform plan`. Read the plan.

## Deploy

```bash
# 1. Create the registry first, so there is somewhere to push the images.
terraform apply -target=google_project_service.apis -target=google_artifact_registry_repository.images
REGISTRY=$(terraform output -raw registry); TAG=$(date +%F)
gcloud auth configure-docker asia-south1-docker.pkg.dev

# 2. Build and push three images (from the repository root). The API image has no OCR libraries.
docker build -t $REGISTRY/backend-api:$TAG --build-arg WITH_OCR=false backend
docker build -t $REGISTRY/backend-worker:$TAG backend
docker build -t $REGISTRY/frontend:$TAG frontend
docker push $REGISTRY/backend-api:$TAG; docker push $REGISTRY/backend-worker:$TAG; docker push $REGISTRY/frontend:$TAG
# put the three image names in terraform.tfvars, then:

# 3. Everything else.
terraform apply

# 4. Secrets Terraform cannot know (printed by `terraform output manual_secrets`):
printf '%s' 'the-smtp-password' | gcloud secrets versions add tarn-smtp-password --data-file=-
printf '%s' 'the-paid-groq-key' | gcloud secrets versions add tarn-groq-api-key --data-file=-   # only for the LLM scorer

# 5. Model weights for the worker (once; `tarn ocr models fetch` and `tarn score models fetch` fill var/models):
gcloud storage cp -r var/models/* gs://<project>-tarn-models/

# 6. Schema, roles, pgvector. Run again after every release that adds a migration.
$(terraform output -raw migrate_command)

# 7. DNS: an A record for the domain to `terraform output load_balancer_ip`. The certificate is
#    issued once DNS resolves (up to an hour).
```

New releases: build and push new tags, change the image variables, `terraform apply`, run the
migration job when the release has migrations.

## Operations

- **A booklet waits for the worker.** In `job` mode the worker starts every `worker_schedule`
  (default 5 minutes), loads the OCR models (about a minute) and works until the queue is
  empty. `gcloud run jobs execute tarn-worker --region asia-south1` starts it at once.
- **Restore.** Cloud SQL console or `gcloud sql instances clone <instance> <new> --point-in-time <UTC time>`
  (7 days) and `gcloud sql backups restore` (14 daily backups). The application and identity
  databases are separate instances: restore the one that was damaged.
- **The password pepper must never change** once users exist (every hash depends on it).
- **The development seed never runs here** (`tarn seed` refuses `TARN_ENV=production`).
- `tarn tenants approve ID --operator NAME` (new colleges wait for it) and `tarn tenants llm`
  need the CLI against production: run it from a Cloud Run job with the `tarn-migrate`
  environment or from a workstation through the Cloud SQL Auth Proxy. There is no job for it yet.

## Not verified (no Google Cloud account yet)

- `terraform validate` passes; `plan` and `apply` have never run. Expect first-apply fixes:
  provider argument changes, API enablement delays, quota.
- **Document AI in asia-south1 is single-region and limited-access**: the OCR processor needs
  Google's request form before it can be created there (create it by hand and set
  `docai_processor`). The engine is off by default.
- **Cloud Run GPUs in asia-south1 are by invitation only** (`worker_gpu`). Without them the
  worker runs on the CPU, which TrOCR makes slow; benchmark first, then choose between asking
  for the GPU, the VM mode (`vm_image` family names change: check it) and another region.
- **Uploads are limited to 32 MiB per request** by Cloud Run over HTTP/1 (`upload_max_bytes`
  defaults to 30 MiB). A 20-page phone-photo booklet can exceed that. Direct-to-bucket uploads
  (signed URLs) would remove the limit and the API's in-memory upload (O39, O98).
- The Cloud Storage event path (`GcsEventPageSource`) is an adapter only: no Eventarc trigger
  or ingest endpoint is deployed (O99).
- The model weights are not pinned (O42); the worker mounts whatever is in the models bucket.
- The images were not built in CI and were never pushed or started on Cloud Run.
