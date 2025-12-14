# Omakase v1 – Architecture Spec (Cloudflare Workers + Cloud Run FastAPI)

## Goals
- Provide a single stable API origin at `https://omakase.thinkwithblack.com`.
- Stream scan progress and results to iOS via SSE.
- Offload heavy/variable-latency AI work to a container runtime (Cloud Run).
- Use Cloudflare for edge protections (WAF/rate limit), routing, and asset delivery.

## Components

### 1) Client (iOS)
- Captures menu image.
- Sends `POST /api/v1/scan/stream`.
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
- Client calls: `https://omakase.thinkwithblack.com/api/v1/scan/stream`
- Worker routes/proxies to Cloud Run:
  - Cloud Run URL (private): e.g. `https://omakase-api-xxxxx.a.run.app/api/v1/scan/stream`

## Data Flow (Scan)
1. iOS uploads base64 image to Worker.
2. Worker forwards request to Cloud Run and streams response back (SSE).
3. Cloud Run emits:
   - `status: analyzing`
   - `menu_data` (items + image_status)
   - `image_update` (per top3 item)
   - `done`
4. iOS displays list first, then replaces top3 placeholders with real images.

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
