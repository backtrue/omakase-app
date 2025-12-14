# Omakase v1 – Cache Spec (SHA256 + Cloudflare Vectorize)

## Goals
- Avoid re-processing identical or near-identical menu photos.
- Be **strict**: only reuse cached results when extremely confident.

## Phase 1: Exact Match (SHA256)
1. Compute SHA256 of the uploaded image bytes (before base64 or after decoding).
2. Lookup by `image_hash`.
3. If hit:
   - return cached `menu_data` immediately
   - emit cached `image_update` events for any available images
   - emit `done`

## Phase 2: Near-Identical Match (Vectorize)
1. If SHA miss, compute an image embedding.
2. Query Vectorize for nearest neighbors.
3. If best similarity >= 0.99:
   - treat as cache hit
   - return cached results
4. Else:
   - treat as new scan

## Data to Cache
For each scan session stored:
- `image_hash`
- `embedding_id` (Vectorize vector id)
- `raw_json_cache` (menu items incl. image_prompt)
- `asset_urls` (generated images per item)
- `created_at`

## Vectorize Notes
- Maintain a single embedding model for consistency.
- Store metadata:
  - `image_hash`
  - `created_at`
  - `scan_id`

## Strictness Rationale
- Menus often change by a single handwritten item or price.
- Loose threshold will cause incorrect menu reuse.
- Start strict; loosen only after measuring false negatives.

## Fallbacks
- If Vectorize is down: continue without near-duplicate cache.
- Never block scans on cache subsystem availability.
