"""
Resumable scan jobs API endpoints.

Endpoints:
- POST /api/v1/uploads/signed-url  -> Get signed URL for direct GCS upload
- POST /api/v1/scan/jobs           -> Create scan job + enqueue Cloud Task
- GET  /api/v1/scan/jobs/{id}      -> Get job snapshot
- GET  /api/v1/scan/jobs/{id}/events -> Resumable SSE stream
- POST /internal/tasks/run-scan    -> Internal task handler (Cloud Tasks only)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from google.cloud import firestore, storage, tasks_v2
from google.protobuf import timestamp_pb2
from pydantic import BaseModel, Field

from .observability import ErrorCode, ScanContext, log_scan_done, log_scan_error, log_scan_start
from .schemas import MenuItem, UserPreferences
from .sse import sse_event

logger = logging.getLogger(__name__)

router = APIRouter()

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
_GCS_BUCKET = os.getenv("GCS_SCAN_BUCKET", "omakase-scans-prod")
_GCP_PROJECT = os.getenv("GCP_PROJECT", "omakase-481015")
_GCP_LOCATION = os.getenv("GCP_LOCATION", "asia-east1")
_CLOUD_TASKS_QUEUE = os.getenv("CLOUD_TASKS_QUEUE", "scan-jobs")
_CLOUD_TASKS_SA_EMAIL = os.getenv(
    "CLOUD_TASKS_SA_EMAIL",
    "cloud-tasks-invoker@omakase-481015.iam.gserviceaccount.com",
)
_CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "https://omakase-api-799819497568.asia-east1.run.app")

_SCAN_EVENTS_TTL_HOURS = 24
_SCAN_SNAPSHOTS_TTL_DAYS = 7

# -----------------------------------------------------------------------------
# Lazy-init clients (avoid cold start overhead if not used)
# -----------------------------------------------------------------------------
_storage_client: Optional[storage.Client] = None
_tasks_client: Optional[tasks_v2.CloudTasksClient] = None
_firestore_client: Optional[firestore.AsyncClient] = None


def _get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client


def _get_tasks_client() -> tasks_v2.CloudTasksClient:
    global _tasks_client
    if _tasks_client is None:
        _tasks_client = tasks_v2.CloudTasksClient()
    return _tasks_client


def _get_firestore_client() -> firestore.AsyncClient:
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.AsyncClient(project=_GCP_PROJECT)
    return _firestore_client


async def _send_push_notification(
    push_token: str,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Send push notification via Expo Push API."""
    import httpx
    
    message = {
        "to": push_token,
        "sound": "default",
        "title": title,
        "body": body,
    }
    if data:
        message["data"] = data
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://exp.host/--/api/v2/push/send",
                json=message,
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
            if response.status_code == 200:
                logger.info("Push notification sent to %s", push_token[:20] + "...")
                return True
            else:
                logger.warning("Push notification failed: %s %s", response.status_code, response.text)
                return False
    except Exception as e:
        logger.exception("Failed to send push notification: %s", e)
        return False


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class SignedUrlRequest(BaseModel):
    content_type: str = Field(default="image/jpeg")
    filename: Optional[str] = None


class SignedUrlResponse(BaseModel):
    upload_url: str
    gcs_uri: str
    expires_at: str


class CreateJobRequest(BaseModel):
    gcs_uri: str
    user_preferences: UserPreferences = Field(default_factory=UserPreferences)
    push_token: Optional[str] = None  # Expo push token for completion notification


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


class JobSnapshot(BaseModel):
    job_id: str
    status: str
    items: List[MenuItem]
    created_at: str
    updated_at: str


class RunScanTaskPayload(BaseModel):
    job_id: str
    gcs_uri: str
    language: str = "zh-TW"
    push_token: Optional[str] = None


# -----------------------------------------------------------------------------
# POST /api/v1/uploads/signed-url
# -----------------------------------------------------------------------------
@router.post("/api/v1/uploads/signed-url", response_model=SignedUrlResponse)
async def create_signed_upload_url(req: SignedUrlRequest) -> SignedUrlResponse:
    """Generate a signed URL for direct GCS upload from mobile client."""
    import google.auth
    from google.auth.transport import requests as auth_requests
    from google.auth import compute_engine

    client = _get_storage_client()
    bucket = client.bucket(_GCS_BUCKET)

    # Generate unique object name
    upload_id = uuid.uuid4().hex
    ext = "jpg" if "jpeg" in req.content_type.lower() else req.content_type.split("/")[-1]
    object_name = f"uploads/{upload_id}.{ext}"

    blob = bucket.blob(object_name)
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)

    # Get credentials and create signing credentials for Cloud Run environment
    credentials, project = google.auth.default()

    # If running on Cloud Run with Compute Engine credentials, use IAM signing
    if hasattr(credentials, "service_account_email"):
        # Refresh credentials to get access token
        auth_request = auth_requests.Request()
        credentials.refresh(auth_request)

        # Use the service account email and access token for signing
        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="PUT",
            content_type=req.content_type,
            service_account_email=credentials.service_account_email,
            access_token=credentials.token,
        )
    else:
        # Local development with service account key
        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="PUT",
            content_type=req.content_type,
        )

    gcs_uri = f"gs://{_GCS_BUCKET}/{object_name}"

    return SignedUrlResponse(
        upload_url=signed_url,
        gcs_uri=gcs_uri,
        expires_at=expires_at.isoformat() + "Z",
    )


# -----------------------------------------------------------------------------
# POST /api/v1/scan/jobs
# -----------------------------------------------------------------------------
@router.post("/api/v1/scan/jobs", response_model=CreateJobResponse)
async def create_scan_job(req: CreateJobRequest) -> CreateJobResponse:
    """Create a new scan job and enqueue a Cloud Task to process it."""
    job_id = uuid.uuid4().hex
    now = datetime.datetime.utcnow()

    # Write initial job document to Firestore
    db = _get_firestore_client()
    job_ref = db.collection("scan_jobs").document(job_id)
    snapshot_ref = db.collection("scan_snapshots").document(job_id)

    expire_at = now + datetime.timedelta(days=_SCAN_SNAPSHOTS_TTL_DAYS)

    job_data = {
        "job_id": job_id,
        "gcs_uri": req.gcs_uri,
        "language": req.user_preferences.language,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "expireAt": expire_at,
    }

    snapshot_data = {
        "job_id": job_id,
        "status": "pending",
        "items": [],
        "created_at": now,
        "updated_at": now,
        "expireAt": expire_at,
    }

    await job_ref.set(job_data)
    await snapshot_ref.set(snapshot_data)

    # Enqueue Cloud Task
    tasks_client = _get_tasks_client()
    queue_path = tasks_client.queue_path(_GCP_PROJECT, _GCP_LOCATION, _CLOUD_TASKS_QUEUE)

    task_payload = RunScanTaskPayload(
        job_id=job_id,
        gcs_uri=req.gcs_uri,
        language=req.user_preferences.language,
        push_token=req.push_token,
    )

    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=f"{_CLOUD_RUN_URL}/internal/tasks/run-scan",
            headers={"Content-Type": "application/json"},
            body=task_payload.model_dump_json().encode(),
            oidc_token=tasks_v2.OidcToken(
                service_account_email=_CLOUD_TASKS_SA_EMAIL,
                audience=_CLOUD_RUN_URL,
            ),
        ),
    )

    try:
        tasks_client.create_task(parent=queue_path, task=task)
        logger.info("Enqueued scan task for job_id=%s", job_id)
    except Exception as e:
        logger.exception("Failed to enqueue task for job_id=%s", job_id)
        # Update job status to failed
        await job_ref.update({"status": "failed", "error": str(e), "updated_at": datetime.datetime.utcnow()})
        raise HTTPException(status_code=500, detail="Failed to enqueue scan task")

    return CreateJobResponse(job_id=job_id, status="pending")


# -----------------------------------------------------------------------------
# GET /api/v1/scan/jobs/{job_id}
# -----------------------------------------------------------------------------
@router.get("/api/v1/scan/jobs/{job_id}", response_model=JobSnapshot)
async def get_job_snapshot(job_id: str) -> JobSnapshot:
    """Get the current snapshot of a scan job."""
    db = _get_firestore_client()
    snapshot_ref = db.collection("scan_snapshots").document(job_id)
    doc = await snapshot_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Job not found")

    data = doc.to_dict()
    items = []
    for item_data in data.get("items", []):
        items.append(MenuItem(**item_data))

    created_at = data.get("created_at")
    updated_at = data.get("updated_at")

    return JobSnapshot(
        job_id=job_id,
        status=data.get("status", "unknown"),
        items=items,
        created_at=created_at.isoformat() + "Z" if hasattr(created_at, "isoformat") else str(created_at),
        updated_at=updated_at.isoformat() + "Z" if hasattr(updated_at, "isoformat") else str(updated_at),
    )


# -----------------------------------------------------------------------------
# GET /api/v1/scan/jobs/{job_id}/events
# -----------------------------------------------------------------------------
@router.get("/api/v1/scan/jobs/{job_id}/events")
async def stream_job_events(
    job_id: str,
    last_event_id: Optional[str] = Query(default=None),
) -> StreamingResponse:
    """
    Stream SSE events for a scan job.
    Supports reconnection via last_event_id query parameter.
    """
    db = _get_firestore_client()

    # Check if job exists
    job_ref = db.collection("scan_jobs").document(job_id)
    job_doc = await job_ref.get()
    if not job_doc.exists:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        from google.cloud.firestore_v1.base_query import FieldFilter
        
        events_ref = db.collection("scan_events").where(filter=FieldFilter("job_id", "==", job_id))

        # If reconnecting, only get events after last_event_id
        start_seq = 0
        if last_event_id:
            try:
                start_seq = int(last_event_id)
                events_ref = events_ref.where(filter=FieldFilter("seq", ">", start_seq))
                logger.info("Reconnecting job_id=%s from seq > %d", job_id, start_seq)
            except ValueError:
                pass

        events_ref = events_ref.order_by("seq")

        # First, replay any existing events
        existing_events = events_ref.stream()
        last_seq = 0
        job_done = False

        async for event_doc in existing_events:
            event_data = event_doc.to_dict()
            event_type = event_data.get("event_type", "unknown")
            payload = event_data.get("payload", {})
            seq = event_data.get("seq", 0)
            last_seq = max(last_seq, seq)

            yield sse_event(event_type, payload, event_id=str(seq))

            if event_type == "done":
                job_done = True

        if job_done:
            return

        # Poll for new events (simple polling approach for MVP)
        poll_interval = 1.0
        max_poll_duration = 300  # 5 minutes max
        poll_start = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - poll_start
            if elapsed >= max_poll_duration:
                yield sse_event("timeout", {"message": "Connection timeout, please reconnect"})
                break

            await asyncio.sleep(poll_interval)

            # Check for new events
            new_events_ref = (
                db.collection("scan_events")
                .where(filter=FieldFilter("job_id", "==", job_id))
                .where(filter=FieldFilter("seq", ">", last_seq))
                .order_by("seq")
            )

            new_events = new_events_ref.stream()
            async for event_doc in new_events:
                event_data = event_doc.to_dict()
                event_type = event_data.get("event_type", "unknown")
                payload = event_data.get("payload", {})
                seq = event_data.get("seq", 0)
                last_seq = max(last_seq, seq)

                yield sse_event(event_type, payload, event_id=str(seq))

                if event_type == "done":
                    return

            # Send heartbeat to keep connection alive
            yield sse_event("heartbeat", {"ts": datetime.datetime.utcnow().isoformat() + "Z"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -----------------------------------------------------------------------------
# POST /internal/tasks/run-scan
# -----------------------------------------------------------------------------
@router.post("/internal/tasks/run-scan")
async def run_scan_task(
    payload: RunScanTaskPayload,
    request: Request,
    x_cloudtasks_taskname: Optional[str] = Header(default=None),
) -> Dict[str, str]:
    """
    Internal endpoint called by Cloud Tasks to run the actual scan.
    This downloads the image from GCS and runs the scan pipeline,
    writing events to Firestore as it progresses.
    """
    job_id = payload.job_id
    gcs_uri = payload.gcs_uri
    language = payload.language

    # Create observability context for this job
    ctx = ScanContext(session_id=job_id, job_id=job_id)
    log_scan_start(ctx, extra={"gcs_uri": gcs_uri, "language": language})

    logger.info("Running scan task for job_id=%s, gcs_uri=%s", job_id, gcs_uri)

    db = _get_firestore_client()
    job_ref = db.collection("scan_jobs").document(job_id)
    snapshot_ref = db.collection("scan_snapshots").document(job_id)

    # Update job status to running
    await job_ref.update({"status": "running", "updated_at": datetime.datetime.utcnow()})
    await snapshot_ref.update({"status": "running", "updated_at": datetime.datetime.utcnow()})

    seq = 0

    async def emit_event(event_type: str, payload_data: Dict[str, Any]) -> None:
        nonlocal seq
        seq += 1
        now = datetime.datetime.utcnow()
        expire_at = now + datetime.timedelta(hours=_SCAN_EVENTS_TTL_HOURS)

        event_doc = {
            "job_id": job_id,
            "seq": seq,
            "event_type": event_type,
            "payload": payload_data,
            "created_at": now,
            "expireAt": expire_at,
        }

        event_ref = db.collection("scan_events").document(f"{job_id}_{seq:06d}")
        await event_ref.set(event_doc)

    async def update_snapshot(status: str, items: List[Dict[str, Any]]) -> None:
        now = datetime.datetime.utcnow()
        await snapshot_ref.update({
            "status": status,
            "items": items,
            "updated_at": now,
        })

    try:
        # Download image from GCS
        await emit_event("status", {"step": "downloading", "message": "正在下載圖片..."})

        storage_client = _get_storage_client()
        # Parse gcs_uri: gs://bucket/path
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        uri_parts = gcs_uri[5:].split("/", 1)
        bucket_name = uri_parts[0]
        object_name = uri_parts[1] if len(uri_parts) > 1 else ""

        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        image_bytes = blob.download_as_bytes()

        if not image_bytes:
            raise ValueError("Downloaded image is empty")

        logger.info("Downloaded %d bytes from %s", len(image_bytes), gcs_uri)

        # Import and run the scan pipeline
        # We'll reuse the existing _stream_scan logic but write to Firestore instead of yielding
        import base64
        from .main import _stream_scan
        from .schemas import ScanRequest

        # Convert image bytes to base64 for the existing scan request format
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        scan_request = ScanRequest(
            image_base64=image_base64,
            user_preferences=UserPreferences(language=language),
        )

        # Run the scan and capture events
        items: List[Dict[str, Any]] = []
        final_status = "completed"

        async for sse_str in _stream_scan(scan_request, job_id=job_id):
            # Parse the SSE string to extract event type and data
            lines = sse_str.strip().split("\n")
            event_type = "unknown"
            data_str = ""

            for line in lines:
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:].strip()

            if not data_str:
                continue

            try:
                event_data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Emit event to Firestore
            await emit_event(event_type, event_data)

            # Update snapshot for menu_data events
            if event_type == "menu_data":
                items = event_data.get("items", [])
                await update_snapshot("running", items)

            # Handle done/error events
            if event_type == "done":
                final_status = event_data.get("status", "completed")
            elif event_type == "error":
                final_status = "failed"

        # Final snapshot update
        await update_snapshot(final_status, items)
        await job_ref.update({"status": final_status, "updated_at": datetime.datetime.utcnow()})

        # Send push notification if token provided
        push_token = payload.push_token
        if push_token and final_status in ("completed", "partial"):
            await _send_push_notification(
                push_token,
                title="菜單翻譯完成！",
                body=f"已翻譯 {len(items)} 道菜品",
                data={"job_id": job_id, "status": final_status},
            )

        # Log completion with observability
        ctx.mark_done(final_status)
        ctx.items_count = len(items)
        log_scan_done(ctx)

        logger.info("Scan task completed for job_id=%s, status=%s", job_id, final_status)
        return {"status": "ok", "job_id": job_id}

    except Exception as e:
        log_scan_error(ctx, ErrorCode.SCAN_FAILED, str(e), exc=e)

        # Emit error event
        await emit_event("error", {
            "code": ErrorCode.SCAN_FAILED.value,
            "message": str(e),
            "recoverable": False,
        })

        # Always emit done event after error to signal stream end
        ctx.mark_done("failed")
        log_scan_done(ctx)
        await emit_event("done", {
            "status": "failed",
            "session_id": job_id,
            "summary": {
                "elapsed_ms": ctx.elapsed_ms(),
                "items_count": 0,
                "used_cache": False,
                "used_fallback": False,
                "unknown_items_count": 0,
            },
        })

        # Update status to failed
        await update_snapshot("failed", [])
        await job_ref.update({
            "status": "failed",
            "error": str(e),
            "updated_at": datetime.datetime.utcnow(),
        })

        return {"status": "error", "job_id": job_id, "error": str(e)}
