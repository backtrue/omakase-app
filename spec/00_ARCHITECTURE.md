# Omakase v1 – Architecture Spec (Cloudflare Workers + Cloud Run FastAPI)

## Goals
- Provide a single stable API origin at `https://omakase.thinkwithblack.com`.
- Stream scan progress and results to iOS via SSE.
- Offload heavy/variable-latency AI work to a container runtime (Cloud Run).
- Use Cloudflare for edge protections (WAF/rate limit), routing, and asset delivery.

## Document Governance (Normative)

This document defines the authoritative v1 architecture and public routing contract.

### Normative References (Source of Truth)
- `spec/00_ARCHITECTURE.md`: system components + routing contract + authoritative public surface.
- `spec/01_API_SSE.md`: public API + SSE event contract (event types, ordering, payload schema).
- `spec/02_STORAGE_R2.md`: asset storage (R2) keys/URLs/cache headers.
- `spec/03_CACHE_VECTORIZE.md`: cache phases + fail-open behavior.
- `spec/04_SECRETS_ENV.md`: environment variables + secret management.

### Informative / Non-normative Documents
- `Omakase-PRD+SDD.md`: product intent and high-level design narrative.
- `spec/12_ENGINEERING_TASKS_P0.md`: backlog/task planning.
- `spec/13_RELEASE_NOTES_DRAFT.md`, `spec/14_APP_STORE_SUBMISSION_CHECKLIST.md`, `spec/15_APP_STORE_LISTING_COPY.md`: release and ops artifacts.

### Change Control
- Any change that affects the **public API**, SSE event payloads, asset URL format, or persistence semantics must update the relevant `spec/*` document(s) in the same PR.
- Backward-compatible additions are allowed (additive fields / additive event types), but removals or semantic changes require versioning (see below).

### Versioning & Deprecation
- Public API uses path versioning (`/api/v1/...`).
- Breaking changes require introducing `/api/v2/...` and a migration plan.
- Deprecations should be documented in `spec/01_API_SSE.md` (status + expected removal window).

## Components

### 1) Client (iOS)
- Captures menu image.
- Normative integration: create a scan job, then consume `GET /api/v1/scan/jobs/{job_id}/events` (SSE).
- Compatibility integration: `POST /api/v1/scan/stream`.
- Consumes `text/event-stream` and incrementally renders:
  - status updates
  - menu items list
  - top-3 image updates

### 2) Cloudflare Workers (Edge Gateway)
Responsibilities:
- Single public entrypoint (`omakase.thinkwithblack.com`).
- Auth/rate limiting (later), request validation, basic logging.
- **Reverse-proxy** the streaming endpoint to Cloud Run while preserving SSE semantics.
- Serve generated assets under `/assets/...` (either redirect to R2 public or proxy/signed URL).

Non-goals (v1):
- Do not run Gemini calls inside Workers.
- Do not do long-running orchestration in Workers.

### 3) Cloud Run (FastAPI Backend)
Responsibilities:
- Implements `POST /api/v1/scan/stream` (SSE).
- Calls Gemini VLM once to produce structured JSON for menu items (OCR + translation + tags + top3 + image prompts).
- Starts top-3 image generation in parallel.
- Uploads images to R2 and emits `image_update` events as each finishes.

### 4) Storage & Cache
- **R2**: store generated images.
- **Cache** (phased):
  - Phase 1: SHA256 hash cache (exact match).
  - Phase 2: Cloudflare Vectorize strict similarity cache (near-identical match).

## Request Routing
- Client calls (normative):
  - `https://omakase.thinkwithblack.com/api/v1/uploads/signed-url`
  - `https://omakase.thinkwithblack.com/api/v1/scan/jobs`
  - `https://omakase.thinkwithblack.com/api/v1/scan/jobs/{job_id}/events`
- Client calls (compatibility): `https://omakase.thinkwithblack.com/api/v1/scan/stream`
- Worker routes/proxies to Cloud Run:
  - Cloud Run URL (private): e.g. `https://omakase-api-xxxxx.a.run.app/api/v1/scan/stream`

## Data Flow (Scan)
1. Client requests a signed URL: `POST /api/v1/uploads/signed-url`.
2. Client uploads the image directly to GCS using the signed URL.
3. Client creates a scan job: `POST /api/v1/scan/jobs`.
4. Client subscribes to job events: `GET /api/v1/scan/jobs/{job_id}/events` (SSE; resumable).
5. Cloud Run emits the normative event contract:
   - `status`
   - `menu_data` (items + image_status)
   - `image_update` (per item as images finish)
   - `done`
6. Client displays list first, then replaces placeholders with real images.

## Asset Delivery
- Cloud Run uploads image to R2 with deterministic key.
- API emits `image_url` under the same domain:
  - `https://omakase.thinkwithblack.com/assets/gen/<session_id>/<item_id>.jpg`
- Worker serves `/assets/...`:
  - v1 option A (fast): public R2 + Worker redirect
  - v1 option B (more private): Worker proxy + optional signed URLs

## Observability (v1 minimal)
- Cloud Run logs:
  - session_id
  - timings: vlm_ms, image_gen_ms
  - error codes
- Worker logs:
  - request_id, status code
  - upstream duration

## Current Implementation Notes (as of 2025-12)

This repo currently includes a **job-based scan pipeline** (normative) alongside the direct streaming endpoint (compatibility).

### Client (Expo / React Native)
- The shipped client in this repo is an **Expo / React Native** app (not SwiftUI).
- Upload strategy (implemented): client uploads the image to GCS via signed URL, then creates a scan job and subscribes to SSE events.

### Job-based Scan API (implemented)
Cloud Run exposes a resumable job API in `backend/app/jobs.py`:
- `POST /api/v1/uploads/signed-url` (direct client upload to GCS)
- `POST /api/v1/scan/jobs` (enqueue Cloud Task)
- `GET /api/v1/scan/jobs/{job_id}` (snapshot polling)
- `GET /api/v1/scan/jobs/{job_id}/events` (SSE stream with replay using `last_event_id`)

Job execution details (implemented):
- Cloud Tasks invokes `POST /internal/tasks/run-scan` to process the uploaded image.
- Events and snapshots are written to Firestore (`scan_jobs`, `scan_events`, `scan_snapshots`) with TTL fields.
- The job API supports an optional Expo `push_token` to notify the user on completion.

This job SSE stream reuses the same event types (`status`, `menu_data`, `image_update`, `done`) as `spec/01_API_SSE.md`.

### Worker + D1 (implemented)
The Worker also implements internal persistence endpoints backed by D1 (see `worker/src/index.ts`):
- `POST /internal/dish_knowledge/fetch`
- `POST /internal/dish_knowledge/upsert_many`
- `POST /internal/scan_records/insert`

Cloud Run can use these internal endpoints as a database fallback via `backend/app/db.py` (`WorkerDb`).
