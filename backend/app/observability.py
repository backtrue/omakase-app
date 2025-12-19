"""
Observability utilities for V1.1a: correlation IDs, timing, error codes.

Usage:
    from .observability import ScanContext, ErrorCode, log_scan_start, log_scan_done

This module provides:
- ScanContext: dataclass for correlation IDs and timing
- ErrorCode: enum of normalized error codes
- Structured logging helpers
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Release metadata (set via env or build)
# -----------------------------------------------------------------------------
RELEASE_VERSION = os.getenv("RELEASE_VERSION", "v1.1a")
GIT_SHA = os.getenv("GIT_SHA", "unknown")


# -----------------------------------------------------------------------------
# Error codes (normalized)
# -----------------------------------------------------------------------------
class ErrorCode(str, Enum):
    """Normalized error codes for SSE error events and logging."""

    # Image input errors
    INVALID_IMAGE_BASE64 = "INVALID_IMAGE_BASE64"
    IMAGE_NOT_MENU = "IMAGE_NOT_MENU"
    IMAGE_TOO_BLURRY = "IMAGE_TOO_BLURRY"

    # VLM errors
    VLM_TIMEOUT = "VLM_TIMEOUT"
    VLM_FAILED = "VLM_FAILED"
    VLM_MODEL_UNAVAILABLE = "VLM_MODEL_UNAVAILABLE"

    # Image generation errors
    IMAGE_GEN_TIMEOUT = "IMAGE_GEN_TIMEOUT"
    IMAGE_GEN_FAILED = "IMAGE_GEN_FAILED"
    IMAGE_PIPELINE_FAILED = "IMAGE_PIPELINE_FAILED"

    # Upstream / infra errors
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    DB_TIMEOUT = "DB_TIMEOUT"
    DB_FAILED = "DB_FAILED"
    GCS_DOWNLOAD_FAILED = "GCS_DOWNLOAD_FAILED"

    # Job errors
    SCAN_FAILED = "SCAN_FAILED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    TASK_ENQUEUE_FAILED = "TASK_ENQUEUE_FAILED"

    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"


# -----------------------------------------------------------------------------
# Scan context (correlation + timing)
# -----------------------------------------------------------------------------
@dataclass
class ScanContext:
    """
    Holds correlation IDs and timing for a single scan request.
    Create at the start of a scan, pass through the pipeline.
    """

    session_id: str
    job_id: Optional[str] = None
    request_id: Optional[str] = None

    # Timing (monotonic, seconds)
    started_at: float = field(default_factory=time.monotonic)

    # Milestones (set as we progress)
    first_menu_data_at: Optional[float] = None
    done_at: Optional[float] = None

    # Step timings (ms)
    vlm_ms: Optional[int] = None
    translate_ms: Optional[int] = None
    image_gen_ms: Optional[int] = None
    db_fetch_ms: Optional[int] = None
    db_write_ms: Optional[int] = None

    # Outcome
    final_status: str = "unknown"
    error_code: Optional[str] = None
    used_cache: bool = False
    used_fallback: bool = False
    items_count: int = 0
    unknown_items_count: int = 0

    # SSE quality (for job-based streams)
    sse_reconnect_count: int = 0
    sse_timeout_count: int = 0

    def elapsed_ms(self) -> int:
        """Total elapsed time since start, in ms."""
        return int((time.monotonic() - self.started_at) * 1000)

    def time_to_first_menu_data_ms(self) -> Optional[int]:
        """Time from start to first menu_data, in ms."""
        if self.first_menu_data_at is None:
            return None
        return int((self.first_menu_data_at - self.started_at) * 1000)

    def time_to_done_ms(self) -> Optional[int]:
        """Time from start to done, in ms."""
        if self.done_at is None:
            return None
        return int((self.done_at - self.started_at) * 1000)

    def mark_first_menu_data(self) -> None:
        """Call when first menu_data is emitted."""
        if self.first_menu_data_at is None:
            self.first_menu_data_at = time.monotonic()

    def mark_done(self, status: str) -> None:
        """Call when done event is emitted."""
        self.done_at = time.monotonic()
        self.final_status = status

    def correlation_fields(self) -> Dict[str, Any]:
        """Return dict of correlation fields for structured logging."""
        fields: Dict[str, Any] = {
            "session_id": self.session_id,
            "release": RELEASE_VERSION,
            "git_sha": GIT_SHA,
        }
        if self.job_id:
            fields["job_id"] = self.job_id
        if self.request_id:
            fields["request_id"] = self.request_id
        return fields

    def timing_fields(self) -> Dict[str, Any]:
        """Return dict of timing fields for structured logging."""
        fields: Dict[str, Any] = {
            "elapsed_ms": self.elapsed_ms(),
        }
        ttfm = self.time_to_first_menu_data_ms()
        if ttfm is not None:
            fields["time_to_first_menu_data_ms"] = ttfm
        ttd = self.time_to_done_ms()
        if ttd is not None:
            fields["time_to_done_ms"] = ttd
        if self.vlm_ms is not None:
            fields["vlm_ms"] = self.vlm_ms
        if self.translate_ms is not None:
            fields["translate_ms"] = self.translate_ms
        if self.image_gen_ms is not None:
            fields["image_gen_ms"] = self.image_gen_ms
        if self.db_fetch_ms is not None:
            fields["db_fetch_ms"] = self.db_fetch_ms
        if self.db_write_ms is not None:
            fields["db_write_ms"] = self.db_write_ms
        return fields

    def outcome_fields(self) -> Dict[str, Any]:
        """Return dict of outcome fields for structured logging."""
        fields: Dict[str, Any] = {
            "final_status": self.final_status,
            "items_count": self.items_count,
            "unknown_items_count": self.unknown_items_count,
            "used_cache": self.used_cache,
            "used_fallback": self.used_fallback,
        }
        if self.error_code:
            fields["error_code"] = self.error_code
        return fields

    def sse_quality_fields(self) -> Dict[str, Any]:
        """Return dict of SSE quality fields for structured logging."""
        return {
            "sse_reconnect_count": self.sse_reconnect_count,
            "sse_timeout_count": self.sse_timeout_count,
        }

    def all_fields(self) -> Dict[str, Any]:
        """Return all fields combined for final summary log."""
        return {
            **self.correlation_fields(),
            **self.timing_fields(),
            **self.outcome_fields(),
            **self.sse_quality_fields(),
        }


# -----------------------------------------------------------------------------
# Structured logging helpers
# -----------------------------------------------------------------------------
def log_scan_start(ctx: ScanContext, extra: Optional[Dict[str, Any]] = None) -> None:
    """Log scan start with correlation fields."""
    fields = ctx.correlation_fields()
    if extra:
        fields.update(extra)
    logger.info("scan_start %s", fields)


def log_scan_done(ctx: ScanContext, extra: Optional[Dict[str, Any]] = None) -> None:
    """Log scan done with all fields (correlation + timing + outcome)."""
    fields = ctx.all_fields()
    if extra:
        fields.update(extra)
    logger.info("scan_done %s", fields)


def log_scan_error(
    ctx: ScanContext,
    error_code: ErrorCode,
    message: str,
    exc: Optional[Exception] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Log scan error with correlation fields and error code."""
    ctx.error_code = error_code.value
    fields = ctx.correlation_fields()
    fields["error_code"] = error_code.value
    fields["error_message"] = message
    if extra:
        fields.update(extra)
    if exc:
        logger.exception("scan_error %s", fields)
    else:
        logger.warning("scan_error %s", fields)


def log_step_timing(
    ctx: ScanContext,
    step: str,
    duration_ms: int,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Log a step timing (e.g., vlm, translate, image_gen)."""
    fields = ctx.correlation_fields()
    fields["step"] = step
    fields["duration_ms"] = duration_ms
    if extra:
        fields.update(extra)
    logger.info("scan_step %s", fields)
