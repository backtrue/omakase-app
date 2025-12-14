# Omakase v1 – API Spec (SSE)

Base URL (public):
- `https://omakase.thinkwithblack.com`

## Endpoint: Stream Scan

### `POST /api/v1/scan/stream`

#### Headers
- `Accept: text/event-stream`
- `Content-Type: application/json`

#### Request Body
```json
{
  "image_base64": "...",
  "user_preferences": {
    "language": "zh-TW"
  }
}
```

#### Response
- HTTP 200
- `Content-Type: text/event-stream; charset=utf-8`
- Response is a stream of SSE events.

### Event Ordering (Typical)
1. `status` (0..n times)
2. `menu_data` (0..1 time)
3. `image_update` (0..n times)
4. `done` (exactly 1 time)

### Event: `status`
Used for progress UI and keep-alive.

Payload:
```json
{
  "step": "uploading" | "analyzing" | "generating_images" | "finalizing",
  "message": "string"
}
```

Recommended cadence:
- emit immediately on connection
- during long steps, emit at least every 10–15 seconds

### Event: `menu_data`
VLM analysis completed. Client can render the menu list immediately.

Payload:
```json
{
  "session_id": "uuid",
  "items": [
    {
      "id": "string",
      "original_name": "string",
      "translated_name": "string",
      "description": "string",
      "tags": ["string"],
      "is_top3": true,
      "image_status": "pending" | "ready" | "none" | "failed",
      "image_prompt": "string"
    }
  ]
}
```

Notes:
- `image_prompt` is included for debugging/tuning in v1. If you later consider it sensitive, remove it from client response.
- For non-top3 items, set `image_status: "none"`.
- For top3 items, initial `image_status: "pending"`.

### Event: `image_update`
Sent per item as images finish (top3 in v1).

Payload:
```json
{
  "session_id": "uuid",
  "item_id": "string",
  "image_status": "ready" | "failed",
  "image_url": "https://omakase.thinkwithblack.com/assets/gen/<session_id>/<item_id>.jpg"
}
```

### Event: `error`
Non-fatal or fatal errors.

Payload:
```json
{
  "code": "string",
  "message": "string",
  "recoverable": true
}
```

Recommended error codes:
- `IMAGE_NOT_MENU`
- `IMAGE_TOO_BLURRY`
- `VLM_FAILED`
- `IMAGE_GEN_FAILED`
- `UPSTREAM_TIMEOUT`

### Event: `done`
Always emitted once to end the stream.

Payload:
```json
{
  "status": "completed" | "failed"
}
```

## Compatibility Notes
- Worker must proxy SSE without buffering.
- Cloud Run must disable response buffering (default FastAPI streaming generator is OK).
- iOS should treat the stream as authoritative; UI state should update per event.
