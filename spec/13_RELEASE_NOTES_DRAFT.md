# Omakase v1 – Release Notes (Draft)

## Scope
This draft summarizes recent and upcoming P0 changes around scan streaming UX, stability, and caching strategy.

## Product Goal ("お通し" Golden Window)
- Users typically receive beer + お通し within ~3 minutes after sitting down.
- Target: deliver **usable** menu results within ~60s.
- Hard cap: always complete within ~180s.

## P0: Progressive Results (Streaming)
- The scan stream should emit partial results as early as possible.
- `menu_data` can be emitted multiple times (0..n) as improved snapshots.
- The UI should show a simple indicator after first results: "正在補完".
- `done` ends the session (no post-`done` background completion).

## P0: SSE Contract Updates
- `menu_data`: now documented as 0..n times.
- `done`: documented with:
  - minimal payload (backward compatible)
  - optional extended payload with `session_id` and `summary` fields

## P0: Menu Cache (MVP1 Fast Path) – Spec Only
- New concept: cache-assisted fast path to reduce latency and token spend.
- Always extract current menu text; reuse translations by `dish_key` + `language`.
- Conservative normalization rules (width/whitespace/punctuation only).
- No prices policy (avoid disputes): prices are not extracted or stored.
- Privacy defaults:
  - do not store raw images
  - store coarse location index (geohash/s2) + accuracy only
  - MVP default: no TTL (to be revisited)

## Runtime / Configuration Notes
- Existing stability work includes separate timeout budget considerations and model fallbacks.
- Environment variables and secrets are defined in `spec/04_SECRETS_ENV.md`.

## Implementation Notes (as of 2025-12)
- In addition to the direct streaming endpoint, the repo includes a job-based/resumable scan pipeline (`backend/app/jobs.py`) with:
  - GCS signed uploads
  - Cloud Tasks orchestration
  - Firestore-backed event replay via `GET /api/v1/scan/jobs/{job_id}/events`

## References
- `spec/01_API_SSE.md`
- `spec/10_MENU_CACHE_GPS_SIMILARITY.md`
- `spec/11_P0_STREAMING_WITH_MENU_CACHE.md`
- `spec/12_ENGINEERING_TASKS_P0.md`
