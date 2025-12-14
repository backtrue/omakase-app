import asyncio
import base64
import io
import os
import uuid
import re
import logging
from typing import AsyncGenerator, Dict, List, Tuple

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response, StreamingResponse
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .gemini_client import GeminiClient
from .image_store import ImageStore
from .schemas import MenuDataEvent, MenuItem, ScanRequest, VlmMenuItem, VlmMenuResponse
from .sse import sse_event

app = FastAPI(title="Omakase API", version="0.1.0")

logger = logging.getLogger(__name__)

_image_store = ImageStore()


_PRIMARY_VLM_MODEL = "gemini-3-pro-preview"
_FALLBACK_VLM_MODEL = "gemini-2.5-pro"

_PRIMARY_IMAGE_MODEL = "gemini-3-pro-image-preview"
_FALLBACK_IMAGE_MODEL = "imagen-3.0-generate-001"


_ONE_BY_ONE_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/"
    "2wCEAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
    "EBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAAR"
    "CAAIAAgDAREAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAM"
    "BAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/AR//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    "9oACAECAQE/AR//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IR//2Q=="
)
_ONE_BY_ONE_JPEG = base64.b64decode(_ONE_BY_ONE_JPEG_BASE64)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/assets/gen/{session_id}/{item_id}.jpg")
def get_generated_asset(session_id: str, item_id: str) -> Response:
    key = f"gen/{session_id}/{item_id}.jpg"
    data = _image_store.get(key)
    if data is None:
        return Response(status_code=404)
    return Response(content=data, media_type="image/jpeg")


def _decode_base64_image(image_base64: str) -> Tuple[bytes, str]:
    # Accept both plain base64 and data URLs like: data:image/jpeg;base64,....
    raw = image_base64.strip()
    mime_type = "image/jpeg"
    if raw.startswith("data:"):
        header, b64 = raw.split(",", 1)
        # data:image/png;base64
        if ";" in header:
            mime_type = header[5:].split(";", 1)[0] or mime_type
        raw = b64

    # Some encoders include newlines.
    raw = raw.replace("\n", "").replace("\r", "")
    return base64.b64decode(raw), mime_type


def _vlm_prompt(language: str) -> str:
    # Keep prompt text-only; schema enforcement is done via response_schema.
    return (
        "Role: 你是精通日本料理歷史與書法的資深美食家。\n"
        "Task: 接收一張手寫菜單圖片，輸出結構化 JSON。\n"
        "Requirements:\n"
        "1) OCR 與推理：若字跡潦草，請根據居酒屋常見菜色與上下文推理修正。\n"
        f"2) 翻譯：將菜名翻譯為 {language}（意譯）。若不確定翻譯，請使用較直覺/常見的意譯，仍需輸出 translated_name。\n"
        "3) 完整性（最優先）：請盡可能列出圖片中所有可辨識的菜色/註記/價錢(若有)，包含小字；不確定時請做最佳猜測並仍輸出。\n"
        "4) 可省略欄位：為了提高完整性，description/tags/image_prompt/romanji 若不確定或太花時間，可以留空字串/空陣列；不要因為要填滿欄位而漏掉菜名。\n"
        "5) 推薦：在不影響完整性的前提下，從已列出的菜色中挑 3 個最推薦的標記 is_top3=true，其餘為 false。\n"
        "6) 內容精簡：description 請控制在 25 字以內；tags 最多 3 個；image_prompt 若提供請用固定模板："
        "Japanese watercolor illustration, hand-drawn style, warm atmosphere, studio ghibli food style, white background. Dish: <ENGLISH NAME>。\n"
        "Output: 僅輸出 JSON（不要 markdown，不要多餘文字）。\n"
    )


def _ensure_jpeg_bytes(image_bytes: bytes) -> bytes:
    # If it's already JPEG, keep as-is.
    if image_bytes[:3] == b"\xff\xd8\xff":
        return image_bytes

    # Common case: Imagen returns PNG bytes.
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _normalize_name_for_dedupe(name: str) -> str:
    lowered = name.strip().lower()
    lowered = re.sub(r"\s+", "", lowered)
    lowered = re.sub(r"[^0-9a-zA-Z\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", "", lowered)
    return lowered


def _split_columns_as_jpeg(image_bytes: bytes) -> List[bytes]:
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return [image_bytes]

    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    if os.getenv("VLM_PREPROCESS", "1") != "0":
        try:
            cutoff = int(os.getenv("VLM_AUTOCONTRAST_CUTOFF", "1"))
        except Exception:
            cutoff = 1
        cutoff = max(0, min(20, cutoff))
        img = ImageOps.autocontrast(img, cutoff=cutoff)

        try:
            contrast = float(os.getenv("VLM_CONTRAST", "1.15"))
        except Exception:
            contrast = 1.15
        if contrast != 1.0:
            img = ImageEnhance.Contrast(img).enhance(contrast)

        try:
            radius = float(os.getenv("VLM_UNSHARP_RADIUS", "1.2"))
            percent = int(float(os.getenv("VLM_UNSHARP_PERCENT", "180")))
            threshold = int(float(os.getenv("VLM_UNSHARP_THRESHOLD", "3")))
        except Exception:
            radius = 1.2
            percent = 180
            threshold = 3
        img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))

    w, h = img.size
    max_dim = int(os.getenv("VLM_IMAGE_MAX_DIM", "1400"))
    jpeg_quality = int(os.getenv("VLM_JPEG_QUALITY", "85"))
    max_segments = int(os.getenv("MAX_VLM_SEGMENTS", "4"))

    def _encode_jpeg(im: Image.Image) -> bytes:
        iw, ih = im.size
        longest = max(iw, ih)
        if longest > max_dim:
            scale = max_dim / max(longest, 1)
            new_w = max(1, int(iw * scale))
            new_h = max(1, int(ih * scale))
            im = im.resize((new_w, new_h), resample=Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=jpeg_quality)
        return out.getvalue()

    segments: List[bytes] = [_encode_jpeg(img)]
    if max_segments <= 1:
        return segments

    remaining = max_segments - 1

    try:
        overlap_ratio = float(os.getenv("VLM_TILE_OVERLAP_RATIO", "0.08"))
    except Exception:
        overlap_ratio = 0.08
    overlap_ratio = max(0.0, min(0.25, overlap_ratio))

    aspect = w / max(h, 1)
    if aspect >= 1.35:
        min_tile_w = int(os.getenv("VLM_MIN_TILE_WIDTH", "420"))
        cols = max(1, int(round(w / max(min_tile_w, 1))))
        cols = min(remaining, max(2, cols))
        step = w / max(cols, 1)
        overlap = int(step * overlap_ratio)
        for c in range(cols):
            left = max(0, int(c * step) - overlap)
            right = min(w, int((c + 1) * step) + overlap)
            segments.append(_encode_jpeg(img.crop((left, 0, right, h))))
    elif (1.0 / max(aspect, 0.0001)) >= 1.35:
        min_tile_h = int(os.getenv("VLM_MIN_TILE_HEIGHT", "420"))
        rows = max(1, int(round(h / max(min_tile_h, 1))))
        rows = min(remaining, max(2, rows))
        step = h / max(rows, 1)
        overlap = int(step * overlap_ratio)
        for r in range(rows):
            top = max(0, int(r * step) - overlap)
            bottom = min(h, int((r + 1) * step) + overlap)
            segments.append(_encode_jpeg(img.crop((0, top, w, bottom))))
    else:
        overlap = int(min(w, h) * overlap_ratio)
        x_mid = w // 2
        y_mid = h // 2
        crops = [
            (0, 0, min(w, x_mid + overlap), min(h, y_mid + overlap)),
            (max(0, x_mid - overlap), 0, w, min(h, y_mid + overlap)),
            (0, max(0, y_mid - overlap), min(w, x_mid + overlap), h),
            (max(0, x_mid - overlap), max(0, y_mid - overlap), w, h),
        ]
        for box in crops[:remaining]:
            segments.append(_encode_jpeg(img.crop(box)))

    return segments[:max_segments]


def _looks_like_model_access_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        s in msg
        for s in [
            "model",
            "not found",
            "does not exist",
            "not available",
            "permission",
            "forbidden",
            "unauthorized",
            "403",
            "404",
            "invalid argument",
        ]
    )


def _mock_menu_items() -> List[MenuItem]:
    return [
        MenuItem(
            id="1",
            original_name="親子丼",
            translated_name="炭火雞肉親子丼",
            description="使用燒烤雞肉與半熟蛋，醬汁濃郁。",
            tags=["雞肉", "主食", "推薦"],
            is_top3=True,
            image_status="pending",
            image_prompt="Japanese watercolor illustration, hand-drawn style, warm atmosphere, studio ghibli food style, white background. Bowl of oyakodon.",
            romanji="oyakodon",
        ),
        MenuItem(
            id="2",
            original_name="焼き鳥 ねぎま",
            translated_name="葱段雞肉串",
            description="雞腿肉與大蔥交錯炭烤，外香內嫩。",
            tags=["串燒", "雞肉", "推薦"],
            is_top3=True,
            image_status="pending",
            image_prompt="Japanese watercolor illustration, hand-drawn style, warm atmosphere, studio ghibli food style, white background. Skewered chicken and green onion yakitori.",
            romanji="yakitori negima",
        ),
        MenuItem(
            id="3",
            original_name="だし巻き玉子",
            translated_name="日式高湯玉子燒",
            description="帶高湯香氣的柔軟玉子燒。",
            tags=["蛋", "小菜", "推薦"],
            is_top3=True,
            image_status="pending",
            image_prompt="Japanese watercolor illustration, hand-drawn style, warm atmosphere, studio ghibli food style, white background. Rolled omelette with dashi.",
            romanji="dashimaki tamago",
        ),
        MenuItem(
            id="4",
            original_name="冷奴",
            translated_name="冰涼豆腐",
            description="清爽的冷豆腐，適合配酒。",
            tags=["豆腐", "小菜"],
            is_top3=False,
            image_status="none",
            image_prompt="Japanese watercolor illustration, hand-drawn style, warm atmosphere, studio ghibli food style, white background. Hiyayakko tofu.",
            romanji="hiyayakko",
        ),
    ]


async def _stream_scan(req: ScanRequest) -> AsyncGenerator[str, None]:
    session_id = str(uuid.uuid4())

    # v1: run in mock mode if GOOGLE_API_KEY is not configured.
    google_api_key = os.getenv("GOOGLE_API_KEY")
    public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    if not google_api_key:
        yield sse_event("status", {"step": "analyzing", "message": "主廚正在解讀手寫字..."})
        items = _mock_menu_items()
        yield sse_event(
            "menu_data",
            MenuDataEvent(session_id=session_id, items=items).model_dump(),
        )

        # simulate image generation completion
        yield sse_event("status", {"step": "generating_images", "message": "主廚正在繪製招牌菜插畫..."})
        for item in [i for i in items if i.is_top3]:
            await asyncio.sleep(0.6)
            key = f"gen/{session_id}/{item.id}.jpg"
            _image_store.put(key, _ONE_BY_ONE_JPEG, content_type="image/jpeg")
            yield sse_event(
                "image_update",
                {
                    "session_id": session_id,
                    "item_id": item.id,
                    "image_status": "ready",
                    "image_url": f"{public_base_url}/assets/gen/{session_id}/{item.id}.jpg",
                },
            )

        yield sse_event("done", {"status": "completed"})
        return

    # Real mode.
    vlm_model = os.getenv("GEMINI_VLM_MODEL", _PRIMARY_VLM_MODEL)
    image_model = os.getenv("GEMINI_IMAGE_MODEL", _PRIMARY_IMAGE_MODEL)

    try:
        image_bytes, mime_type = _decode_base64_image(req.image_base64)
    except Exception:
        yield sse_event(
            "error",
            {"code": "INVALID_IMAGE_BASE64", "message": "圖片格式不正確，請重新拍攝/上傳", "recoverable": True},
        )
        yield sse_event("done", {"status": "failed"})
        return

    client = GeminiClient(api_key=google_api_key, vlm_model=vlm_model, image_model=image_model)
    yield sse_event("status", {"step": "analyzing", "message": "主廚正在解讀手寫字..."})

    vlm_timeout_s = float(os.getenv("VLM_TIMEOUT_SECONDS", "240"))
    vlm_fallback_timeout_s = float(os.getenv("VLM_FALLBACK_TIMEOUT_SECONDS", "60"))
    image_timeout_s = float(os.getenv("IMAGE_TIMEOUT_SECONDS", "60"))

    segments = _split_columns_as_jpeg(image_bytes)
    per_segment_timeout_s = float(os.getenv("VLM_SEGMENT_TIMEOUT_SECONDS", "75"))
    heartbeat_s = float(os.getenv("SSE_HEARTBEAT_SECONDS", "10"))
    max_consecutive_no_new = int(os.getenv("VLM_MAX_CONSECUTIVE_NO_NEW", str(max(3, len(segments)))))

    loop = asyncio.get_running_loop()
    start_time = loop.time()
    primary_deadline = start_time + vlm_timeout_s
    overall_deadline = primary_deadline + max(0.0, vlm_fallback_timeout_s)

    try:
        prompt = _vlm_prompt(req.user_preferences.language)
        seen: set[str] = set()
        merged: List[VlmMenuItem] = []

        stop_after_timeout = False
        consecutive_no_new = 0

        for idx, seg in enumerate(segments):
            if stop_after_timeout:
                break

            if loop.time() >= primary_deadline:
                break

            if len(segments) > 1:
                yield sse_event(
                    "status",
                    {"step": "analyzing", "message": f"主廚正在解讀手寫字...({idx + 1}/{len(segments)})"},
                )

            task = asyncio.create_task(
                client.parse_menu_from_image_async(
                    image_bytes=seg,
                    mime_type="image/jpeg",
                    prompt=prompt,
                )
            )

            try:
                start_t = loop.time()
                segment_deadline = min(start_t + per_segment_timeout_s, primary_deadline)
                while True:
                    remaining = segment_deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    done, _ = await asyncio.wait({task}, timeout=min(heartbeat_s, remaining))
                    if task in done:
                        result = task.result()
                        break
                    if len(segments) > 1:
                        yield sse_event(
                            "status",
                            {
                                "step": "analyzing",
                                "message": f"主廚正在解讀手寫字...({idx + 1}/{len(segments)})",
                            },
                        )
                    else:
                        yield sse_event("status", {"step": "analyzing", "message": "主廚正在解讀手寫字..."})
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if len(segments) > 1:
                    yield sse_event(
                        "status",
                        {"step": "analyzing", "message": f"部分區塊解析逾時，繼續...({idx + 1}/{len(segments)})"},
                    )
                else:
                    yield sse_event(
                        "status",
                        {"step": "analyzing", "message": "部分區塊解析逾時，繼續..."},
                    )
                continue
            except Exception:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise

            before = len(merged)
            for m in result.menu_items:
                key = _normalize_name_for_dedupe(m.original_name) or _normalize_name_for_dedupe(m.translated_name)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(m)

            added = len(merged) - before
            if added == 0:
                consecutive_no_new += 1
            else:
                consecutive_no_new = 0

            if consecutive_no_new >= max_consecutive_no_new and merged:
                break

            if idx == 0 and len(segments) == 1 and len(merged) >= 12:
                break

        if not merged:
            raise asyncio.TimeoutError("no segments produced items")

        vlm_result = VlmMenuResponse(menu_items=merged)
    except asyncio.TimeoutError as e:
        logger.exception("VLM timeout (model=%s)", vlm_model)
        if vlm_model == _PRIMARY_VLM_MODEL:
            yield sse_event(
                "status",
                {
                    "step": "analyzing",
                    "message": f"主模型解析逾時，改用 {_FALLBACK_VLM_MODEL} 解析...",
                },
            )
            client = GeminiClient(api_key=google_api_key, vlm_model=_FALLBACK_VLM_MODEL, image_model=image_model)
            try:
                prompt = _vlm_prompt(req.user_preferences.language)
                seen: set[str] = set()
                merged: List[VlmMenuItem] = []

                stop_after_timeout = False
                consecutive_no_new = 0

                for idx, seg in enumerate(segments):
                    if stop_after_timeout:
                        break

                    if loop.time() >= overall_deadline:
                        break

                    if len(segments) > 1:
                        yield sse_event(
                            "status",
                            {
                                "step": "analyzing",
                                "message": f"主廚正在解讀手寫字...({idx + 1}/{len(segments)})",
                            },
                        )

                    task = asyncio.create_task(
                        client.parse_menu_from_image_async(
                            image_bytes=seg,
                            mime_type="image/jpeg",
                            prompt=prompt,
                        )
                    )

                    try:
                        start_t = loop.time()
                        segment_deadline = min(start_t + per_segment_timeout_s, overall_deadline)
                        while True:
                            remaining = segment_deadline - loop.time()
                            if remaining <= 0:
                                raise asyncio.TimeoutError()
                            done, _ = await asyncio.wait({task}, timeout=min(heartbeat_s, remaining))
                            if task in done:
                                result = task.result()
                                break
                            if len(segments) > 1:
                                yield sse_event(
                                    "status",
                                    {
                                        "step": "analyzing",
                                        "message": f"主廚正在解讀手寫字...({idx + 1}/{len(segments)})",
                                    },
                                )
                            else:
                                yield sse_event(
                                    "status",
                                    {"step": "analyzing", "message": "主廚正在解讀手寫字..."},
                                )
                    except asyncio.TimeoutError:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        if len(segments) > 1:
                            yield sse_event(
                                "status",
                                {"step": "analyzing", "message": f"部分區塊解析逾時，繼續...({idx + 1}/{len(segments)})"},
                            )

                        else:
                            yield sse_event(
                                "status",
                                {"step": "analyzing", "message": "部分區塊解析逾時，繼續..."},
                            )
                        continue
                    except Exception:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise

                    before = len(merged)
                    for m in result.menu_items:
                        key = _normalize_name_for_dedupe(m.original_name) or _normalize_name_for_dedupe(m.translated_name)
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        merged.append(m)

                    added = len(merged) - before
                    if added == 0:
                        consecutive_no_new += 1
                    else:
                        consecutive_no_new = 0

                    if consecutive_no_new >= max_consecutive_no_new and merged:
                        break

                    if idx == 0 and len(segments) == 1 and len(merged) >= 12:
                        break

                if not merged:
                    raise asyncio.TimeoutError("no segments produced items")

                vlm_result = VlmMenuResponse(menu_items=merged)
            except asyncio.TimeoutError as e2:
                yield sse_event(
                    "error",
                    {
                        "code": "VLM_TIMEOUT",
                        "message": "解析逾時：請稍後重試或換一張更清晰的照片",
                        "detail": str(e2),
                        "recoverable": True,
                    },
                )
                yield sse_event("done", {"status": "failed"})
                return
            except Exception as e2:
                logger.exception("VLM fallback failed (model=%s)", _FALLBACK_VLM_MODEL)
                yield sse_event(
                    "error",
                    {
                        "code": "VLM_FAILED",
                        "message": "解析失敗：請確認圖片清晰且為菜單",
                        "detail": str(e2),
                        "recoverable": True,
                    },
                )
                yield sse_event("done", {"status": "failed"})
                return
        else:
            yield sse_event(
                "error",
                {
                    "code": "VLM_TIMEOUT",
                    "message": "解析逾時：請稍後重試或換一張更清晰的照片",
                    "detail": str(e),
                    "recoverable": True,
                },
            )
            yield sse_event("done", {"status": "failed"})
            return
    except Exception as e:
        logger.exception("VLM failed (model=%s)", vlm_model)
        # If preview model isn't enabled on the API key/project, fallback automatically.
        if vlm_model == _PRIMARY_VLM_MODEL:
            fallback_reason = (
                f"模型 {_PRIMARY_VLM_MODEL} 暫不可用"
                if _looks_like_model_access_error(e)
                else "主模型解析失敗"
            )
            yield sse_event(
                "status",
                {
                    "step": "analyzing",
                    "message": f"{fallback_reason}，改用 {_FALLBACK_VLM_MODEL} 解析...",
                },
            )
            client = GeminiClient(api_key=google_api_key, vlm_model=_FALLBACK_VLM_MODEL, image_model=image_model)
            try:
                prompt = _vlm_prompt(req.user_preferences.language)
                seen: set[str] = set()
                merged: List[VlmMenuItem] = []

                stop_after_timeout = False
                consecutive_no_new = 0

                for idx, seg in enumerate(segments):
                    if stop_after_timeout:
                        break

                    if loop.time() >= overall_deadline:
                        break

                    if len(segments) > 1:
                        yield sse_event(
                            "status",
                            {
                                "step": "analyzing",
                                "message": f"主廚正在解讀手寫字...({idx + 1}/{len(segments)})",
                            },
                        )

                    task = asyncio.create_task(
                        client.parse_menu_from_image_async(
                            image_bytes=seg,
                            mime_type="image/jpeg",
                            prompt=prompt,
                        )
                    )

                    try:
                        start_t = loop.time()
                        segment_deadline = min(start_t + per_segment_timeout_s, overall_deadline)
                        while True:
                            remaining = segment_deadline - loop.time()
                            if remaining <= 0:
                                raise asyncio.TimeoutError()
                            done, _ = await asyncio.wait({task}, timeout=min(heartbeat_s, remaining))
                            if task in done:
                                result = task.result()
                                break
                            if len(segments) > 1:
                                yield sse_event(
                                    "status",
                                    {
                                        "step": "analyzing",
                                        "message": f"主廚正在解讀手寫字...({idx + 1}/{len(segments)})",
                                    },
                                )
                            else:
                                yield sse_event(
                                    "status",
                                    {"step": "analyzing", "message": "主廚正在解讀手寫字..."},
                                )
                    except asyncio.TimeoutError:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        if len(segments) > 1:
                            yield sse_event(
                                "status",
                                {"step": "analyzing", "message": f"部分區塊解析逾時，繼續...({idx + 1}/{len(segments)})"},
                            )

                        else:
                            yield sse_event(
                                "status",
                                {"step": "analyzing", "message": "部分區塊解析逾時，繼續..."},
                            )
                        continue
                    except Exception:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise

                    before = len(merged)
                    for m in result.menu_items:
                        key = _normalize_name_for_dedupe(m.original_name) or _normalize_name_for_dedupe(m.translated_name)
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        merged.append(m)

                    added = len(merged) - before
                    if added == 0:
                        consecutive_no_new += 1
                    else:
                        consecutive_no_new = 0

                    if consecutive_no_new >= max_consecutive_no_new and merged:
                        break

                    if idx == 0 and len(segments) == 1 and len(merged) >= 12:
                        break

                if not merged:
                    raise asyncio.TimeoutError("no segments produced items")

                vlm_result = VlmMenuResponse(menu_items=merged)
            except asyncio.TimeoutError as e2:
                yield sse_event(
                    "error",
                    {
                        "code": "VLM_TIMEOUT",
                        "message": "解析逾時：請稍後重試或換一張更清晰的照片",
                        "detail": str(e2),
                        "recoverable": True,
                    },
                )
                yield sse_event("done", {"status": "failed"})
                return
            except Exception as e2:
                logger.exception("VLM fallback failed (model=%s)", _FALLBACK_VLM_MODEL)
                yield sse_event(
                    "error",
                    {
                        "code": "VLM_FAILED",
                        "message": "解析失敗：請確認圖片清晰且為菜單",
                        "detail": str(e2),
                        "recoverable": True,
                    },
                )
                yield sse_event("done", {"status": "failed"})
                return
        else:
            yield sse_event(
                "error",
                {
                    "code": "VLM_FAILED",
                    "message": "解析失敗：請確認圖片清晰且為菜單",
                    "detail": str(e),
                    "recoverable": True,
                },
            )
            yield sse_event("done", {"status": "failed"})
            return

    items: List[MenuItem] = []
    for idx, m in enumerate(vlm_result.menu_items, start=1):
        is_top3 = bool(m.is_top3)
        description = (m.description or "").strip()

        raw_tags = m.tags or []
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags = []

        image_prompt = (m.image_prompt or "").strip()
        if is_top3 and not image_prompt:
            dish_name = (m.translated_name or m.original_name or "").strip()
            image_prompt = (
                "Japanese watercolor illustration, hand-drawn style, warm atmosphere, "
                "studio ghibli food style, white background. Dish: "
                f"{dish_name}."
            ).strip()

        romanji = (m.romanji or "").strip()
        items.append(
            MenuItem(
                id=str(idx),
                original_name=m.original_name,
                translated_name=m.translated_name,
                description=description,
                tags=tags,
                is_top3=is_top3,
                image_status="pending" if is_top3 else "none",
                image_prompt=image_prompt,
                romanji=romanji,
            )
        )

    yield sse_event("menu_data", MenuDataEvent(session_id=session_id, items=items).model_dump())

    top3 = [i for i in items if i.is_top3]
    if not top3:
        yield sse_event("done", {"status": "completed"})
        return

    yield sse_event("status", {"step": "generating_images", "message": "主廚正在繪製招牌菜插畫..."})

    try:
        async def _gen_one(item: MenuItem) -> tuple[MenuItem, bytes | None, Exception | None]:
            try:
                img = await asyncio.wait_for(
                    asyncio.to_thread(client.generate_food_image_bytes, prompt=item.image_prompt),
                    timeout=image_timeout_s,
                )
                return item, img, None
            except Exception as e:
                return item, None, e

        tasks = [asyncio.create_task(_gen_one(item)) for item in top3]
        image_fallback_announced = False

        for done_task in asyncio.as_completed(tasks):
            item, img_bytes, err = await done_task
            item_id = item.id

            if err is None and img_bytes is not None:
                img_bytes = _ensure_jpeg_bytes(img_bytes)
                key = f"gen/{session_id}/{item_id}.jpg"
                _image_store.put(key, img_bytes, content_type="image/jpeg")
                yield sse_event(
                    "image_update",
                    {
                        "session_id": session_id,
                        "item_id": item_id,
                        "image_status": "ready",
                        "image_url": f"{public_base_url}/assets/gen/{session_id}/{item_id}.jpg",
                    },
                )
                continue

            if isinstance(err, asyncio.TimeoutError):
                yield sse_event(
                    "image_update",
                    {
                        "session_id": session_id,
                        "item_id": item_id,
                        "image_status": "failed",
                        "image_url": "",
                    },
                )
                continue

            if err is not None and _looks_like_model_access_error(err) and image_model == _PRIMARY_IMAGE_MODEL:
                if not image_fallback_announced:
                    yield sse_event(
                        "status",
                        {
                            "step": "generating_images",
                            "message": f"模型 {_PRIMARY_IMAGE_MODEL} 暫不可用，改用 {_FALLBACK_IMAGE_MODEL} 生圖...",
                        },
                    )
                    image_fallback_announced = True
                try:
                    fallback_client = GeminiClient(
                        api_key=google_api_key,
                        vlm_model=getattr(client, "vlm_model", vlm_model),
                        image_model=_FALLBACK_IMAGE_MODEL,
                    )
                    fb = await asyncio.wait_for(
                        asyncio.to_thread(
                            fallback_client.generate_food_image_bytes,
                            prompt=item.image_prompt,
                        ),
                        timeout=image_timeout_s,
                    )
                    fb = _ensure_jpeg_bytes(fb)
                    key = f"gen/{session_id}/{item_id}.jpg"
                    _image_store.put(key, fb, content_type="image/jpeg")
                    yield sse_event(
                        "image_update",
                        {
                            "session_id": session_id,
                            "item_id": item_id,
                            "image_status": "ready",
                            "image_url": f"{public_base_url}/assets/gen/{session_id}/{item_id}.jpg",
                        },
                    )
                    continue
                except Exception:
                    pass

            yield sse_event(
                "image_update",
                {
                    "session_id": session_id,
                    "item_id": item_id,
                    "image_status": "failed",
                    "image_url": "",
                },
            )

        yield sse_event("done", {"status": "completed"})
    except Exception as e:
        yield sse_event(
            "error",
            {
                "code": "IMAGE_PIPELINE_FAILED",
                "message": "生圖流程失敗",
                "detail": str(e),
                "recoverable": True,
            },
        )
        yield sse_event("done", {"status": "failed"})

        


@app.post("/api/v1/scan/stream")
async def scan_stream(
    req: ScanRequest,
    accept: str | None = Header(default=None),
) -> StreamingResponse:
    if accept and "text/event-stream" not in accept:
        raise HTTPException(status_code=406, detail="Client must send Accept: text/event-stream")

    generator = _stream_scan(req)
    return StreamingResponse(generator, media_type="text/event-stream")
