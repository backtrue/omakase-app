# Omakase v1 – Engineering Tasks (P0)

This document breaks down the P0 work into actionable engineering tasks across Backend (Cloud Run), Worker (Cloudflare), and iOS.

Normative references:
- `spec/01_API_SSE.md`
- `spec/10_MENU_CACHE_GPS_SIMILARITY.md`
- `spec/11_P0_STREAMING_WITH_MENU_CACHE.md`

## Objectives (P0)
- Progressive results: show partial menu results early, keep improving until `done`.
- UX target: first usable results ~60s; hard cap ~180s.
- Fast path: MVP1 item-level reuse (`dish_key` + `language`).
- Safety path: translate unknown items via VLM; always end stream.

## Deliverables
- Backend emits `menu_data` 0..n times and `done` once.
- iOS merges repeated `menu_data` snapshots and shows "正在補完".
- Worker proxies SSE without buffering.
- Optional: `done.summary` for lightweight diagnostics.

## Backend (Cloud Run / FastAPI)
### P0-Backend-1: Progressive `menu_data` snapshots
- Implement ability to emit `menu_data` multiple times per scan.
- Define a stable merge key per item:
  - Preferred: stable server id.
  - Fallback: `dish_key` + `first_seen_index`.
- Ensure list ordering is stable (avoid UI thrash).

### P0-Backend-2: Time budgeting (UX budget vs HTTP timeout)
- Enforce a UX budget independent from HTTP/client timeouts:
  - Aim to emit first usable `menu_data` within ~60s.
  - Hard stop within ~180s; emit best-effort results then `done`.

### P0-Backend-3: Cache-assisted fast path (MVP1)
- Always extract current menu text (OCR/text extraction step).
- Create conservative `dish_key` normalization:
  - normalize width, whitespace, punctuation
  - no kana/kanji conversion
- Lookup DishKnowledge by (`dish_key`, `language`) and reuse translation fields.
- Emit first `menu_data` as soon as >=1 item available.

### P0-Backend-4: Translate only unknown items (safety path)
- For dish_keys missing translations:
  - translate with VLM
  - merge results into the snapshot
  - periodically emit updated `menu_data`

### P0-Backend-5: Learning write path
- Upsert DishKnowledge by (`dish_key`, `language`).
- Merge strategy:
  - do not blindly overwrite
  - use `seen_count` heuristic to prefer higher-trust entries
- Store coarse geo only (geohash/s2 + accuracy), no lat/lng.
- No raw image storage.

### P0-Backend-6: Optional diagnostics in `done.summary`
- Implement `done.summary` fields:
  - `elapsed_ms`, `items_count`, `used_cache`, `used_fallback`, `unknown_items_count`

## Worker (Cloudflare)
### P0-Worker-1: SSE proxy correctness
- Verify Worker does not buffer SSE.
- Preserve headers:
  - `Content-Type: text/event-stream`
  - `Cache-Control: no-cache`
- Ensure upstream disconnect propagates correctly.

### P0-Worker-2: Minimal request tracing (optional)
- Add request id header to upstream and log upstream duration.

## iOS
### P0-iOS-1: Merge repeated `menu_data`
- Treat `menu_data` as snapshot updates (0..n).
- Merge by server stable id (or fallback key) without reordering unexpectedly.

### P0-iOS-2: UX messaging
- After first results, show a light indicator: "正在補完".
- On `done` with partial results, show suggestion to re-capture.

### P0-iOS-3: Client telemetry (optional)
- Record:
  - upload image size
  - connect-to-first-event
  - connect-to-first-menu_data

## Acceptance Criteria (P0)
- A scan never leaves UI in indefinite loading state.
- `done` is always received.
- When any items are extractable, at least one `menu_data` is emitted.
- Repeated `menu_data` updates do not cause jarring UI reordering.

## Out of Scope (P0)
- Venue selection UI and points/rewards.
- Vectorize-based authoritative menu-level cache hits.
- Price extraction.
