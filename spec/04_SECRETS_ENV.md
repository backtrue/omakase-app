# Omakase v1 – Secrets & Env Spec

## Domains
- Public API domain: `omakase.thinkwithblack.com`

## Cloud Run (FastAPI) Environment Variables
- `GOOGLE_API_KEY` (required): Gemini API key
- `GEMINI_VLM_MODEL` (default: `gemini-3-pro-preview`): model name for OCR/translation
- `GEMINI_IMAGE_MODEL` (default: `gemini-3-pro-image-preview`): model name for image generation

Fallback behavior:
- If the preview models are not enabled for the API key/project, the backend will automatically fallback to:
  - VLM: `gemini-2.5-pro`
  - Image: `imagen-3.0-generate-001`
- You can always override by explicitly setting `GEMINI_VLM_MODEL` / `GEMINI_IMAGE_MODEL`.

- `PUBLIC_BASE_URL` (required): `https://omakase.thinkwithblack.com`

Job-based scan pipeline (implemented in `backend/app/jobs.py`):
- `GCS_SCAN_BUCKET` (required for uploads/jobs): GCS bucket name for uploaded scans
- `GCP_PROJECT` (required for Cloud Tasks/Firestore): GCP project id
- `GCP_LOCATION` (required for Cloud Tasks): region, e.g. `asia-east1`
- `CLOUD_TASKS_QUEUE` (required for jobs): queue name
- `CLOUD_TASKS_SA_EMAIL` (required for jobs): service account used for OIDC token
- `CLOUD_RUN_URL` (required for jobs): base URL of this Cloud Run service (used as task target + OIDC audience)

- `R2_ENDPOINT` (required): S3-compatible endpoint for R2
- `R2_ACCESS_KEY_ID` (required)
- `R2_SECRET_ACCESS_KEY` (required)
- `R2_BUCKET` (required): `omakase-assets`
- `R2_PUBLIC_ASSET_PREFIX` (default): `/assets/`

Database fallback via Worker (implemented in `backend/app/db.py`):
- `WORKER_BASE_URL` (optional): base URL of Worker (defaults to `PUBLIC_BASE_URL` if set)
- `INTERNAL_API_TOKEN` (optional): shared token for Worker `/internal/*` endpoints

- `VECTORIZE_ENABLED` (default: false for phase-1)
- `VECTORIZE_INDEX` (optional): Vectorize index name
- `VECTORIZE_API_TOKEN` (optional): if needed by your integration approach

- `LOG_LEVEL` (default): `INFO`

## Cloudflare Worker Environment / Secrets
- `UPSTREAM_CLOUD_RUN_URL` (required): full base URL of Cloud Run service

Internal endpoints auth (implemented in `worker/src/index.ts`):
- `INTERNAL_API_TOKEN` (required if you use `/internal/*` endpoints)

Optional (later):
- `WORKER_API_KEY` (if you want to lock down iOS access)
- R2 binding config (if Worker serves assets via binding)

## Cloudflare D1
If you enable the internal persistence endpoints, configure a D1 binding named `DB` in Wrangler/Cloudflare.

## Security
- Never commit secrets into repo.
- Prefer Cloud Run Secrets Manager / Cloudflare secrets.
