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

- `R2_ENDPOINT` (required): S3-compatible endpoint for R2
- `R2_ACCESS_KEY_ID` (required)
- `R2_SECRET_ACCESS_KEY` (required)
- `R2_BUCKET` (required): `omakase-assets`
- `R2_PUBLIC_ASSET_PREFIX` (default): `/assets/`

- `VECTORIZE_ENABLED` (default: false for phase-1)
- `VECTORIZE_INDEX` (optional): Vectorize index name
- `VECTORIZE_API_TOKEN` (optional): if needed by your integration approach

- `LOG_LEVEL` (default): `INFO`

## Cloudflare Worker Environment / Secrets
- `UPSTREAM_CLOUD_RUN_URL` (required): full base URL of Cloud Run service

Optional (later):
- `WORKER_API_KEY` (if you want to lock down iOS access)
- R2 binding config (if Worker serves assets via binding)

## Security
- Never commit secrets into repo.
- Prefer Cloud Run Secrets Manager / Cloudflare secrets.
