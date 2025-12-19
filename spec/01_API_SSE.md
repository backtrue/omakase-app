# Omakase v1 – API Spec (SSE)

Base URL (public):
- `https://omakase.thinkwithblack.com`

## Normative API Decision (v1)

### Normative client integration: resumable job workflow

For v1, the normative client integration is the **resumable job-based workflow**:
- `POST /api/v1/uploads/signed-url`
- `POST /api/v1/scan/jobs`
- `GET /api/v1/scan/jobs/{job_id}/events` (SSE, resumable)
- `GET /api/v1/scan/jobs/{job_id}` (snapshot polling)

The **normative contract** is the SSE event schema defined in this document.

### Legacy / compatibility endpoint

`POST /api/v1/scan/stream` is treated as a compatibility endpoint:
- It must emit the same core event types (`status`, `menu_data`, `image_update`, `done`) with the same payload schemas.
- It is not guaranteed to be resumable.

## Compatibility & Evolution Rules (Normative)

### Backward compatibility (v1)
- Clients must ignore unknown fields in `data` JSON payloads.
- Clients must ignore unknown `event` types.
- Producers may add new fields and new event types in v1 (additive changes only).

### Breaking changes
- Breaking changes require a new API version (e.g. `/api/v2/...`) and a migration plan.

### Deprecation process
- Mark endpoints or fields as deprecated in this document with a target removal window.
- Prefer additive replacement first, then deprecate.

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
2. `menu_data` (0..n times)
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

Payload (minimal):
```json
{
  "status": "completed" | "failed"
}
```

Payload (extended, optional; backward-compatible):
```json
{
  "status": "completed" | "failed",
  "session_id": "uuid",
  "summary": {
    "elapsed_ms": 12345,
    "items_count": 12,
    "used_cache": true,
    "used_fallback": false,
    "unknown_items_count": 3
  }
}
```

## Compatibility Notes
- Worker must proxy SSE without buffering.
- Cloud Run must disable response buffering (default FastAPI streaming generator is OK).
- iOS should treat the stream as authoritative; UI state should update per event.

## Implemented Alternative: Resumable Job Stream (MVP)

This repo also implements a job-based workflow that reuses the **same event types and payload schemas** defined above, but makes the stream resumable.

### Create + Run a job
- `POST /api/v1/uploads/signed-url`
- `POST /api/v1/scan/jobs`

Implementation details:
- The job creation request supports an optional Expo `push_token` for completion notifications.
- Jobs are executed by Cloud Tasks via an internal handler: `POST /internal/tasks/run-scan`.

### Consume events (SSE)

#### `GET /api/v1/scan/jobs/{job_id}/events`

Resumable behavior:
- Each SSE event includes an `id` set to an increasing sequence number.
- Clients can reconnect using:
  - `last_event_id` query parameter (integer), and the server will replay only events with `seq > last_event_id`.

Additional events (implementation detail):
- `heartbeat`: periodic keep-alive emitted by the job stream while polling for new events.
- `timeout`: emitted when the job stream polling window ends; clients should reconnect.
