# Omakase v1 – On-demand Image Generation Spec

## Goal
- Implement PRD "Lazy Loading": non-top3 items generate images only when the user requests them.

## Proposed API (v1.1)
Option A (recommended): new endpoint
- `POST /api/v1/scan/{session_id}/items/{item_id}/image`

Request body (optional)
```json
{
  "style": "watercolor"
}
```

Response
- HTTP 202 accepted
- Image generation result is delivered via existing SSE channel as an `image_update` event.

## Behavior
- Idempotency:
  - If the image for the item already exists, backend should immediately emit `image_update` with `image_status=ready` and the existing `image_url`.
- Concurrency:
  - Backend should limit parallel on-demand generations per session.

## SSE contract
Reuse `image_update`:
```json
{
  "session_id": "uuid",
  "item_id": "string",
  "image_status": "ready" | "failed",
  "image_url": "https://omakase.thinkwithblack.com/assets/gen/<session_id>/<item_id>.jpg"
}
```

## Errors
- If generation fails, emit `image_update` with `image_status=failed`.
- Consider a dedicated error code for visibility:
  - `IMAGE_GEN_FAILED`

## Security & rate limits
- Worker should apply rate limiting per device/session.
- Optional: require `WORKER_API_KEY` for write actions.
