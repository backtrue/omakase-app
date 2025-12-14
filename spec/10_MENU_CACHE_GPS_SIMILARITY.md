# Omakase v1 – Menu Cache Spec (GPS + Image Similarity)

## Goals
- Reduce time-to-first-usable-result (TTFR) by reusing prior knowledge for the *same* restaurant/menu.
- Reduce token spend by reusing translations and descriptions for previously seen dishes.
- Improve accuracy over time via accumulation (more scans => better matches and better item-level translations).

## Non-goals (v1)
- Do not guarantee full menu reconstruction from cache alone.
- Do not infer or output prices.
- Do not implement a full points/rewards system (only reserve interfaces).

## Key Idea
A scan produces **menu-level** and **item-level** knowledge.
A future scan can use:
1) **Location context** (GPS) to narrow candidates.
2) **Image similarity** (strict) to detect near-duplicate menus.
3) **Text overlap** (OCR tokens) to reuse prior translations for matching dish names.

This enables:
- **Fast path**: show partial results quickly (cached items) while continuing to scan/translate the rest.
- **Cost control**: only translate unknown/new items.

## Inputs
### Location
Preferred source:
- App-derived device location (with user permission): latitude/longitude + accuracy.
Secondary source:
- EXIF GPS extracted from image if present.
Future:
- User-selected venue/restaurant (explicit confirmation, can be tied to points).

### Image
- Image bytes (decoded from base64) used for hashing and/or embedding.

## Data Model
### 1) ScanRecord (menu-level)
- `scan_id` (uuid)
- `created_at`
- `image_hash_sha256` (exact match)
- `image_embedding_id` (Vectorize id, optional)
- `geo`:
  - `accuracy_m` (optional)
  - `geohash` or `s2_cell` (recommended; default)
- `language` (target language)
- `items_raw` (raw structured items from VLM, excluding prices)
- `items_normalized` (server-normalized for dedupe)
- `assets` (top3 image URLs if generated)

### 2) DishKnowledge (item-level)
- `dish_key` (canonical key: normalized Japanese name)
- `original_name_examples` (set)
- `translations_by_language`:
  - `en`: { `translated_name`, `description`, `tags`, `updated_at`, `source_scan_id` }
  - ...
- `stats`:
  - `seen_count`
  - `last_seen_at`
  - `last_seen_geo` (coarse)

## Matching Pipeline (Read Path)
### Phase 0: Candidate narrowing by location
- If location present, search prior ScanRecords in nearby region:
  - radius based on `accuracy_m` (e.g. max(200m, accuracy_m * 2))
  - coarse index: geohash/s2
- If no location, skip this narrowing and proceed with stricter matching.

### Phase 1: Exact image match (SHA256)
- Compute `sha256(image_bytes)`.
- If hit:
  - Treat as a *strong hint* that this is a previously seen menu photo.
  - Use it to boost ranking / fetch related ScanRecord and DishKnowledge early.
  - Still proceed with Phase 3 (text overlap reuse) to reflect the current menu contents.

### Phase 2: Near-identical image match (Vectorize – strict)
- Compute image embedding.
- Query Vectorize within location-narrowed candidates (if supported) or global.
- If similarity >= strict threshold (e.g. 0.99):
  - Use as a *ranking / hint* signal only.
  - Do not treat image similarity as authoritative menu-level cache hit in v1.

### Phase 3: Text overlap reuse (OCR-first, item-level reuse)
If Phase 1/2 miss:
- Run a fast OCR/text extraction step (can be VLM-lite or OCR engine).
- Normalize extracted Japanese dish strings into `dish_key` candidates.
- For each `dish_key`:
  - If DishKnowledge exists for the target language, reuse translation/description/tags.
  - Otherwise, translate only the missing ones.

Result composition:
- Emit partial `menu_data` from reused items first.
- Continue scanning/translating the remaining items.

## Write Path (Learning)
On every scan completion:
- Create/Upsert ScanRecord.
- Update DishKnowledge per item:
  - Merge names (do not overwrite good translations with worse ones).
  - Update stats and last_seen.

## Normalization Rules
### `dish_key` normalization (conservative)
- Normalize Unicode width (full-width/half-width) where possible.
- Trim and collapse whitespace.
- Remove punctuation/symbols commonly introduced by OCR.
- Do not attempt kana/kanji conversions in v1.

## Upsert / Merge Strategy
### DishKnowledge updates
- Do not blindly overwrite existing translations.
- Merge-by-language with provenance:
  - Keep the current translation unless the new candidate is from a trusted source or is explicitly confirmed (future).
  - Track `source_scan_id`, `updated_at`, and `seen_count`.

### Trusted source heuristic (v1)
- Increase trust as `seen_count` grows.
- Prefer higher `seen_count` entries when choosing between competing translations.

## Quality & Safety
### Strictness
- Prefer false negatives (miss cache) over false positives (wrong menu).
- Any menu-level reuse should be strict (hash or very high similarity).
- Item-level reuse should be gated by exact or high-confidence text normalization.

### No Prices Policy
- Prices are excluded from extraction and storage.
- If any numeric strings are present, they are ignored.

## Privacy & Retention
### Why not store raw images (default)
Even menu photos can contain:
- Incidental faces, reflections, IDs/credit cards on table, receipts, phone screens.
- Location/time metadata revealing user habits.

Default v1 recommendation:
- Do **not** store raw images.
- Store only:
  - `sha256` hash
  - an embedding/vector representation
  - derived text tokens
  - coarse location index (geohash/s2)

Retention:
- MVP default: no TTL for ScanRecords.
- DishKnowledge can be retained longer since it is de-identified.

## Reserved Interfaces (Future)
### Venue selection
- Optional request field: `venue_hint` (string or venue_id).
- Optional response field: `points_awarded` and `actions_available`.

### Points / Rewards (not implemented)
- Actions may include: "select venue", "confirm corrections", "upload clearer photo".
- Points can be redeemed in a future store.

## Failure / Degradation
- If cache subsystems (Vectorize/DB) are down:
  - Do not block scanning.
  - Fall back to the normal VLM scan pipeline.

## MVP Scope (P0)
### MVP1 (v1)
- Implement **item-level reuse** first:
  - Always scan current menu text.
  - Reuse DishKnowledge by `dish_key` (normalized Japanese name) and language.
  - Translate only unknown items.
- Location is used for candidate narrowing (geohash default; radius by `accuracy_m`).
- Image similarity (Vectorize) is optional and used as ranking/hint only.
- No venue selection UI and no points logic; keep reserved request/response fields only.
