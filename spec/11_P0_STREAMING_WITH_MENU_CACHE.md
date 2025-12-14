# Omakase v1 – P0 Spec: Progressive Results with Menu Cache (MVP1 Fast Path)

## Goals
- Deliver a **usable** menu experience within the user's "golden window".
- Make the app feel alive (no indefinite spinner) by emitting partial results early.
- Reduce cost and latency by reusing prior translations when available.

## UX Targets
- **Primary target**: first usable results within ~60s.
- **Hard cap**: always terminate within ~180s (no indefinite waiting).

## Definitions
- **TTFR** (Time To First Result): time from request start to the first `menu_data` event that contains >=1 item.
- **Fast path (cache-assisted)**: reuse item-level translations from the database based on dish text.
- **Safety path (model-assisted)**: use VLM translation for unknown items and/or when cache miss is high.

## Non-goals (P0)
- No price extraction.
- No full venue selection UI.
- No points/rewards system (only reserve interfaces).
- No guaranteed menu-level cache hit.

## High-Level Strategy
The scan pipeline is always:
1) **Extract current menu text** (always).
2) **Reuse what we already know** (DishKnowledge by `dish_key` + language).
3) **Translate only unknown items** (model).
4) **Emit partial `menu_data` ASAP**, then keep improving until done.

## Client UX Contract
### Progressive rendering
- The client should render the first `menu_data` immediately.
- Subsequent `menu_data` events represent an updated snapshot and should be merged.

### Suggested UI messaging (non-normative)
- After first results: "正在補完"
- If done with partial: "已先提供可辨識內容；若要更完整，建議重拍/放大/調整角度。"

## API / SSE Contract
This spec assumes the existing SSE endpoint and event types in `spec/01_API_SSE.md`.

### Required behavior
- Emit `status` events regularly during processing.
- Emit **at least one** `menu_data` if any items are extracted.
- Always emit `done`.

### Recommended additions (backward-compatible)
- Include a `session_id` in `status` and `done` payloads.
- Include `summary` in `done` payload:
  - `elapsed_ms`
  - `items_count`
  - `used_cache` (true/false)
  - `used_fallback` (true/false)
  - `unknown_items_count`

## Merge Policy (Client + Server)
### Identity
- Preferred: server provides a stable per-item identifier.
- P0 fallback: `dish_key` + `first_seen_index` can serve as a stable key.

### Update rules
- "Fill blanks" is preferred over destructive overwrites.
- Allow appending newly discovered items.
- Avoid frequent reshuffling of list order.

## Pipeline Details
### Step 0: Preprocessing
- Decode image.
- Compute `sha256(image_bytes)` (hint only).
- Extract location context:
  - Prefer device location (with permission).
  - Fall back to EXIF GPS if available.
  - Store only coarse location index (geohash/s2) + accuracy.

### Step 1: Text extraction (always)
- Extract Japanese dish strings from the current menu.
- Produce conservative `dish_key` values:
  - Normalize width, whitespace, and punctuation.
  - Do not perform kana/kanji conversion.

### Step 2: Cache reuse (MVP1 fast path)
- For each `dish_key`:
  - Lookup DishKnowledge by (`dish_key`, `language`).
  - If found: reuse `translated_name`, `description`, `tags`.
- Emit first `menu_data` as soon as at least one item is available.

### Step 3: Translate unknown items (safety path)
- For unknown items only:
  - Call VLM/translation model.
  - Merge translations into the current item list.
  - Periodically emit updated `menu_data` snapshots.

### Step 4: Termination
- Stop when:
  - No further improvements are expected, or
  - Hard cap reached.
- Emit `done` with summary.
- `done` ends the session. No further background completion is expected after `done`.

## Timeout Budgeting (P0)
- Maintain a **UX budget** distinct from model HTTP timeouts.
- Suggested:
  - Aim: produce first `menu_data` within ~60s.
  - Hard cap: stop at ~180s.

## Storage / Learning (Write Path)
- On completion, upsert DishKnowledge:
  - Merge-by-language with provenance.
  - Use `seen_count` as trust heuristic.
- Update ScanRecord metadata (coarse geo, hashes, embedding ids if used later).

## Failure / Degradation
- If cache/database unavailable:
  - Skip reuse; proceed with model-assisted translation.
- If model fails/timeouts:
  - If any cached/partial items exist, emit them and `done`.
  - Otherwise emit an `error` event, then `done`.
