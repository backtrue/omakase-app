# Omakase v1 – Observability & SLO Spec

## Goals
- Make failures diagnosable without guessing.
- Track latency and success rates for scanning and image generation.

## Key metrics
Backend (Cloud Run):
- `scan_requests_total`
- `scan_success_total`
- `scan_failed_total` by `error_code`
- `vlm_latency_ms` (per request)
- `image_gen_latency_ms` (per item)
- `sse_stream_duration_ms`

Worker:
- request id
- upstream duration
- status code

Client (iOS):
- upload image size
- SSE connect-to-first-event time
- connect-to-menu_data time

## Logging contract
- Every scan should include:
  - `session_id`
  - model names (vlm/image)
  - timeout settings
  - key error codes

## SLO proposals (initial)
- P95 time-to-menu_data < 45s
- P99 time-to-menu_data < 120s
- Overall success rate > 95% on reference dataset

## Alerts (later)
- Spike in `VLM_FAILED` or `UPSTREAM_TIMEOUT`.
- P95 latency regression.
