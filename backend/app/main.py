import asyncio
import base64
import hashlib
import io
import json
import os
import uuid
import re
import logging
import unicodedata
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence, Tuple

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response, StreamingResponse
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .db import fetch_dish_knowledge, insert_scan_record, open_db, upsert_dish_knowledge_many
from .gemini_client import GeminiClient
from .image_store import ImageStore
from .schemas import MenuDataEvent, MenuItem, ScanRequest, VlmMenuItem, VlmMenuResponse
from .sse import sse_event
from .jobs import router as jobs_router

app = FastAPI(title="Omakase API", version="0.1.0")
app.include_router(jobs_router)

logger = logging.getLogger(__name__)

_image_store = ImageStore()

_PRIMARY_VLM_MODEL = "gemini-2.5-pro"
_FALLBACK_VLM_MODEL = "gemini-2.5-flash"

_PRIMARY_IMAGE_MODEL = "gemini-3-pro-image-preview"
_FALLBACK_IMAGE_MODEL = "imagen-3.0-generate-001"

_ONE_BY_ONE_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/"
    "2wCEAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
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
    raw = image_base64.strip()
    mime_type = "image/jpeg"
    if raw.startswith("data:"):
        header, b64 = raw.split(",", 1)
        if ";" in header:
            mime_type = header[5:].split(";", 1)[0] or mime_type
        raw = b64

    raw = raw.replace("\n", "").replace("\r", "")
    return base64.b64decode(raw), mime_type

def _vlm_prompt(language: str) -> str:
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

def _ocr_prompt() -> str:
    return (
        "Role: 你是日本居酒屋手寫菜單 OCR 專家。\n"
        "Task: 從圖片中擷取所有可辨識的日文菜名字串，並輸出結構化 JSON。\n"
        "Requirements:\n"
        "1) 請只列出菜名/品項名稱（不需要價錢）。\n"
        "2) 若有重複或疑似同一品項的不同寫法，仍可輸出，但請盡量保持原始字面。\n"
        "3) 請避免輸出空字串。\n"
        "Output: 僅輸出 JSON（不要 markdown，不要多餘文字）。\n"
    )

def _translate_prompt(*, language: str, dish_strings: Sequence[str]) -> str:
    lines = [s for s in dish_strings if isinstance(s, str) and s.strip()]
    items = []
    for s in lines:
        original = s.strip()
        dish_key = _normalize_dish_key(original)
        if not dish_key:
            continue
        items.append({"dish_key": dish_key, "original_name": original})
    joined = "\n".join(f"- {json.dumps(it, ensure_ascii=False)}" for it in items)
    return (
        "Role: 你是精通日本料理的翻譯與說明撰稿人。\n"
        "Task: 將提供的日文菜名逐一翻譯為目標語言，輸出結構化 JSON。\n"
        "Requirements:\n"
        "1) 請只翻譯下列提供的品項，不要新增未提供的品項。\n"
        "2) `dish_key` 必須與輸入一致（不要改）。\n"
        "3) `original_name` 必須與輸入一致（不要自行修正成不同菜名）。\n"
        f"4) `translated_name` 請翻譯成 {language}（意譯）。若不確定仍需給出最直覺的意譯。\n"
        "5) `description` 可留空字串；若填寫請控制在 25 字內。\n"
        "6) `tags` 最多 3 個，若不確定可為空陣列。\n"
        "7) 若輸入品項數量 >= 3，請在其中挑選最多 3 個最推薦的標記 `is_top3=true`；其餘為 false。\n"
        "8) `image_prompt`、`romanji` 可留空。\n"
        "Input dish items (JSON lines):\n"
        f"{joined}\n"
        "Output: 僅輸出 JSON（不要 markdown，不要多餘文字）。\n"
    )

def _ensure_jpeg_bytes(image_bytes: bytes) -> bytes:
    if image_bytes[:3] == b"\xff\xd8\xff":
        return image_bytes

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

def _normalize_dish_key(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", (name or "").strip())
    normalized = normalized.lower()
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"[^0-9a-zA-Z\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", "", normalized)
    return normalized

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

    google_api_key = os.getenv("GOOGLE_API_KEY")
    public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    try:
        ux_first_result_s = float(os.getenv("UX_FIRST_RESULT_SECONDS", "60"))
    except Exception:
        ux_first_result_s = 60.0
    try:
        ux_hard_cap_s = float(os.getenv("UX_HARD_CAP_SECONDS", "180"))
    except Exception:
        ux_hard_cap_s = 180.0
    try:
        menu_data_min_interval_s = float(os.getenv("MENU_DATA_MIN_INTERVAL_SECONDS", "1.5"))
    except Exception:
        menu_data_min_interval_s = 1.5

    ux_deadline = started_at + max(1.0, ux_hard_cap_s)
    first_result_deadline = started_at + max(1.0, ux_first_result_s)

    used_cache = False
    used_fallback = False
    final_status: str = "failed"

    emitted_fatal_error = False

    menu_data_emitted = False
    last_menu_data_ts = 0.0

    item_order: List[str] = []
    items_by_key: Dict[str, MenuItem] = {}
    next_item_id = 1

    def _status_payload(step: str, message: str) -> Dict[str, Any]:
        return {"step": step, "message": message, "session_id": session_id}

    def _snapshot_items() -> List[MenuItem]:
        return [items_by_key[k] for k in item_order if k in items_by_key]

    def _upsert_menu_item_from_vlm(m: VlmMenuItem) -> bool:
        nonlocal next_item_id

        original_name = (m.original_name or "").strip()
        translated_name = (m.translated_name or "").strip()
        key_original = _normalize_name_for_dedupe(original_name)
        key_translated = _normalize_name_for_dedupe(translated_name)

        if key_original and key_original in items_by_key:
            key = key_original
        elif key_translated and key_translated in items_by_key:
            key = key_translated
        else:
            key = key_original or key_translated

        if not key:
            return False

        is_top3 = bool(m.is_top3)
        description = (m.description or "").strip()

        raw_tags = m.tags or []
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags = []

        image_prompt = (m.image_prompt or "").strip()
        if is_top3 and not image_prompt:
            dish_name = (translated_name or original_name or "").strip()
            image_prompt = (
                "Japanese watercolor illustration, hand-drawn style, warm atmosphere, "
                "studio ghibli food style, white background. Dish: "
                f"{dish_name}."
            ).strip()

        romanji = (m.romanji or "").strip()

        if key not in items_by_key:
            items_by_key[key] = MenuItem(
                id=str(next_item_id),
                original_name=original_name,
                translated_name=translated_name,
                description=description,
                tags=tags,
                is_top3=is_top3,
                image_status="pending" if is_top3 else "none",
                image_prompt=image_prompt,
                romanji=romanji,
            )
            item_order.append(key)
            next_item_id += 1
            return True

        item = items_by_key[key]
        changed = False

        if original_name and not item.original_name.strip():
            item.original_name = original_name
            changed = True
        if translated_name and not item.translated_name.strip():
            item.translated_name = translated_name
            changed = True
        if description and not item.description.strip():
            item.description = description
            changed = True
        if tags and not item.tags:
            item.tags = tags
            changed = True
        if romanji and not item.romanji.strip():
            item.romanji = romanji
            changed = True
        if is_top3 and not item.is_top3:
            item.is_top3 = True
            if item.image_status == "none":
                item.image_status = "pending"
            changed = True
        if item.is_top3 and image_prompt and not item.image_prompt.strip():
            item.image_prompt = image_prompt
            changed = True

        return changed

    try:
        yield sse_event("status", _status_payload("analyzing", "主廚正在解讀手寫字..."))

        if not google_api_key:
            items = _mock_menu_items()
            for item in items:
                key = _normalize_name_for_dedupe(item.original_name) or item.id
                if key in items_by_key:
                    key = f"{key}:{item.id}"
                items_by_key[key] = item
                item_order.append(key)
            yield sse_event(
                "menu_data",
                MenuDataEvent(session_id=session_id, items=items).model_dump(),
            )
            menu_data_emitted = True
            last_menu_data_ts = loop.time()

            top3 = [i for i in items if i.is_top3]
            if top3 and loop.time() < ux_deadline:
                yield sse_event("status", _status_payload("generating_images", "主廚正在繪製招牌菜插畫..."))
                for item in top3:
                    if loop.time() >= ux_deadline:
                        break
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

            final_status = "completed"
        if google_api_key:
            vlm_model = os.getenv("GEMINI_VLM_MODEL", _PRIMARY_VLM_MODEL)
            image_model = os.getenv("GEMINI_IMAGE_MODEL", _PRIMARY_IMAGE_MODEL)

            try:
                image_bytes, mime_type = _decode_base64_image(req.image_base64)
            except Exception:
                yield sse_event(
                    "error",
                    {"code": "INVALID_IMAGE_BASE64", "message": "圖片格式不正確，請重新拍攝/上傳", "recoverable": True},
                )
                emitted_fatal_error = True
                image_bytes = b""
                mime_type = "image/jpeg"

            image_hash_sha256 = hashlib.sha256(image_bytes).hexdigest() if image_bytes else ""

            client: GeminiClient | None = None
            vlm_exc: Exception | None = None

            try:
                vlm_timeout_s = float(os.getenv("VLM_TIMEOUT_SECONDS", "240"))
            except Exception:
                vlm_timeout_s = 240.0
            try:
                vlm_fallback_timeout_s = float(os.getenv("VLM_FALLBACK_TIMEOUT_SECONDS", "60"))
            except Exception:
                vlm_fallback_timeout_s = 60.0
            try:
                image_timeout_s = float(os.getenv("IMAGE_TIMEOUT_SECONDS", "60"))
            except Exception:
                image_timeout_s = 60.0
            try:
                db_timeout_s = float(os.getenv("DB_TIMEOUT_SECONDS", "20"))
            except Exception:
                db_timeout_s = 20.0

            segments = _split_columns_as_jpeg(image_bytes) if image_bytes else []
            try:
                per_segment_timeout_s = float(os.getenv("VLM_SEGMENT_TIMEOUT_SECONDS", "75"))
            except Exception:
                per_segment_timeout_s = 75.0
            try:
                heartbeat_s = float(os.getenv("SSE_HEARTBEAT_SECONDS", "10"))
            except Exception:
                heartbeat_s = 10.0

            primary_deadline = min(started_at + max(0.0, vlm_timeout_s), ux_deadline)
            overall_deadline = min(primary_deadline + max(0.0, vlm_fallback_timeout_s), ux_deadline)

            attempts: List[tuple[str, float, bool]] = []
            if segments and vlm_model == _PRIMARY_VLM_MODEL:
                attempts = [
                    (vlm_model, primary_deadline, False),
                    (_FALLBACK_VLM_MODEL, overall_deadline, True),
                ]
            elif segments:
                attempts = [(vlm_model, overall_deadline, False)]

            ocr_prompt = _ocr_prompt()

            def _ensure_item_for_dish(dish_key: str, original_name: str) -> bool:
                nonlocal next_item_id

                if not dish_key:
                    return False
                if dish_key in items_by_key:
                    return False
                items_by_key[dish_key] = MenuItem(
                    id=str(next_item_id),
                    original_name=original_name.strip(),
                    translated_name="",
                    description="",
                    tags=[],
                    is_top3=False,
                    image_status="none",
                    image_prompt="",
                    romanji="",
                )
                item_order.append(dish_key)
                next_item_id += 1
                return True

            if not segments:
                vlm_exc = RuntimeError("no image bytes")
            else:
                for attempt_idx, (attempt_model, attempt_deadline, is_fallback_attempt) in enumerate(attempts):
                    if loop.time() >= ux_deadline:
                        break
                    if (not menu_data_emitted) and loop.time() >= first_result_deadline:
                        vlm_exc = asyncio.TimeoutError()
                        break
                    if is_fallback_attempt:
                        used_fallback = True
                    try:
                        client = GeminiClient(api_key=google_api_key, vlm_model=attempt_model, image_model=image_model)

                        for seg_idx, seg in enumerate(segments):
                            if loop.time() >= attempt_deadline or loop.time() >= ux_deadline:
                                break
                            if (not menu_data_emitted) and loop.time() >= first_result_deadline:
                                vlm_exc = asyncio.TimeoutError()
                                break

                            if len(segments) > 1:
                                yield sse_event(
                                    "status",
                                    _status_payload("analyzing", f"主廚正在辨識菜名...({seg_idx + 1}/{len(segments)})"),
                                )

                            task = asyncio.create_task(
                                client.parse_dish_strings_from_image_async(
                                    image_bytes=seg,
                                    mime_type="image/jpeg",
                                    prompt=ocr_prompt,
                                )
                            )

                            try:
                                start_t = loop.time()
                                segment_deadline = min(start_t + per_segment_timeout_s, attempt_deadline, ux_deadline)
                                if not menu_data_emitted:
                                    segment_deadline = min(segment_deadline, first_result_deadline)
                                while True:
                                    remaining = segment_deadline - loop.time()
                                    if remaining <= 0:
                                        raise asyncio.TimeoutError()
                                    done, _ = await asyncio.wait({task}, timeout=min(heartbeat_s, remaining))
                                    if task in done:
                                        ocr_result = task.result()
                                        break
                                    if len(segments) > 1:
                                        yield sse_event(
                                            "status",
                                            _status_payload(
                                                "analyzing",
                                                f"主廚正在辨識菜名...({seg_idx + 1}/{len(segments)})",
                                            ),
                                        )
                                    else:
                                        yield sse_event("status", _status_payload("analyzing", "主廚正在辨識菜名..."))
                            except asyncio.TimeoutError as e:
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                                vlm_exc = e
                                if loop.time() >= min(attempt_deadline, ux_deadline):
                                    break
                                continue
                            except asyncio.CancelledError:
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                                raise
                            except Exception as e:
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                                raise e

                            added_any = False
                            for s in getattr(ocr_result, "dish_strings", []) or []:
                                if not isinstance(s, str) or not s.strip():
                                    continue
                                dish_key = _normalize_dish_key(s)
                                added_any = _ensure_item_for_dish(dish_key, s) or added_any

                            if added_any and items_by_key:
                                now = loop.time()
                                if (not menu_data_emitted) or (now - last_menu_data_ts >= menu_data_min_interval_s):
                                    yield sse_event(
                                        "menu_data",
                                        MenuDataEvent(session_id=session_id, items=_snapshot_items()).model_dump(),
                                    )
                                    menu_data_emitted = True
                                    last_menu_data_ts = now

                            if loop.time() >= ux_deadline:
                                break

                        if items_by_key:
                            break
                        if attempt_idx < len(attempts) - 1:
                            yield sse_event(
                                "status",
                                _status_payload("analyzing", f"主模型暫不可用，改用 {_FALLBACK_VLM_MODEL} 辨識..."),
                            )
                            continue
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.exception("OCR failed (model=%s)", attempt_model)
                        vlm_exc = e
                        if items_by_key:
                            break
                        if attempt_idx < len(attempts) - 1:
                            fallback_reason = f"模型 {_PRIMARY_VLM_MODEL} 暫不可用" if _looks_like_model_access_error(e) else "主模型辨識失敗"
                            yield sse_event(
                                "status",
                                _status_payload("analyzing", f"{fallback_reason}，改用 {_FALLBACK_VLM_MODEL} 辨識..."),
                            )
                            continue
                        break

            if items_by_key:
                try:
                    async def _db_fetch_knowledge() -> Dict[str, Dict[str, Any]]:
                        async with open_db() as conn:
                            if conn is None:
                                return {}
                            return await fetch_dish_knowledge(
                                conn,
                                dish_keys=list(item_order),
                                language=req.user_preferences.language,
                            )

                    remaining_budget = max(0.0, ux_deadline - loop.time())
                    knowledge: Dict[str, Dict[str, Any]] = {}
                    if remaining_budget > 0:
                        knowledge = await asyncio.wait_for(
                            _db_fetch_knowledge(),
                            timeout=min(db_timeout_s, remaining_budget),
                        )

                    if knowledge:
                        changed = False
                        for dish_key in item_order:
                            item = items_by_key.get(dish_key)
                            if item is None:
                                continue
                            k = knowledge.get(dish_key)
                            if not k:
                                continue
                            if (k.get("translated_name") or "").strip() and not item.translated_name.strip():
                                item.translated_name = str(k.get("translated_name") or "")
                                changed = True
                                used_cache = True
                            if (k.get("description") or "").strip() and not item.description.strip():
                                item.description = str(k.get("description") or "")
                                changed = True
                                used_cache = True
                            tags = list(k.get("tags") or [])
                            if tags and not item.tags:
                                item.tags = [str(t).strip() for t in tags if str(t).strip()]
                                changed = True
                                used_cache = True
                            if (k.get("romanji") or "").strip() and not item.romanji.strip():
                                item.romanji = str(k.get("romanji") or "")
                                changed = True
                                used_cache = True

                        if changed and items_by_key:
                            now = loop.time()
                            if (not menu_data_emitted) or (now - last_menu_data_ts >= menu_data_min_interval_s):
                                yield sse_event(
                                    "menu_data",
                                    MenuDataEvent(session_id=session_id, items=_snapshot_items()).model_dump(),
                                )
                                menu_data_emitted = True
                                last_menu_data_ts = now
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    logger.exception("DishKnowledge fetch failed")

                unknown = [k for k in item_order if k in items_by_key and not items_by_key[k].translated_name.strip()]
                if unknown and client is not None and loop.time() < ux_deadline:
                    yield sse_event("status", _status_payload("analyzing", "主廚正在翻譯未知菜色..."))
                    translate_prompt = _translate_prompt(
                        language=req.user_preferences.language,
                        dish_strings=[items_by_key[k].original_name for k in unknown if k in items_by_key],
                    )

                    def _make_translate_task(model_name: str) -> asyncio.Task[VlmMenuResponse]:
                        translate_client = GeminiClient(api_key=google_api_key, vlm_model=model_name, image_model=image_model)
                        return asyncio.create_task(translate_client.translate_menu_items_async(prompt=translate_prompt))

                    translation: Optional[VlmMenuResponse] = None
                    for model_name in [vlm_model, _FALLBACK_VLM_MODEL] if vlm_model == _PRIMARY_VLM_MODEL else [vlm_model]:
                        if loop.time() >= min(overall_deadline, ux_deadline):
                            break
                        if model_name != vlm_model:
                            used_fallback = True
                        task = _make_translate_task(model_name)
                        try:
                            while True:
                                remaining = min(overall_deadline, ux_deadline) - loop.time()
                                if remaining <= 0:
                                    raise asyncio.TimeoutError()
                                done, _ = await asyncio.wait({task}, timeout=min(heartbeat_s, remaining))
                                if task in done:
                                    translation = task.result()
                                    break
                                yield sse_event("status", _status_payload("analyzing", "主廚正在翻譯未知菜色..."))
                        except asyncio.TimeoutError as e:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            vlm_exc = e
                            translation = None
                        except Exception as e:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            vlm_exc = e
                            translation = None

                        if translation is not None:
                            break

                    if translation is not None:
                        changed = False
                        for m in translation.menu_items:
                            key_from_model = (getattr(m, "dish_key", None) or "").strip()
                            original_name = (m.original_name or "").strip()

                            dish_key = _normalize_dish_key(key_from_model or original_name)
                            item = items_by_key.get(dish_key)
                            if item is None:
                                continue
                            if (m.translated_name or "").strip() and not item.translated_name.strip():
                                item.translated_name = (m.translated_name or "").strip()
                                changed = True
                            if (m.description or "").strip() and not item.description.strip():
                                item.description = (m.description or "").strip()
                                changed = True
                            tags = m.tags or []
                            if isinstance(tags, list) and tags and not item.tags:
                                item.tags = [str(t).strip() for t in tags if str(t).strip()]
                                changed = True
                            if (m.romanji or "").strip() and not item.romanji.strip():
                                item.romanji = (m.romanji or "").strip()
                                changed = True
                            if bool(m.is_top3) and not item.is_top3:
                                item.is_top3 = True
                                item.image_status = "pending"
                                item.image_prompt = (m.image_prompt or "").strip() or item.image_prompt
                                if item.is_top3 and not item.image_prompt.strip():
                                    dish_name = (item.translated_name or item.original_name or "").strip()
                                    item.image_prompt = (
                                        "Japanese watercolor illustration, hand-drawn style, warm atmosphere, "
                                        "studio ghibli food style, white background. Dish: "
                                        f"{dish_name}."
                                    ).strip()
                                changed = True

                        if changed and items_by_key:
                            now = loop.time()
                            if (not menu_data_emitted) or (now - last_menu_data_ts >= menu_data_min_interval_s):
                                yield sse_event(
                                    "menu_data",
                                    MenuDataEvent(session_id=session_id, items=_snapshot_items()).model_dump(),
                                )
                                menu_data_emitted = True
                                last_menu_data_ts = now

                if items_by_key and not menu_data_emitted:
                    yield sse_event(
                        "menu_data",
                        MenuDataEvent(session_id=session_id, items=_snapshot_items()).model_dump(),
                    )
                    menu_data_emitted = True
                    last_menu_data_ts = loop.time()

                try:
                    async def _db_write_scan_and_knowledge() -> None:
                        async with open_db() as conn:
                            if conn is None:
                                return
                            await insert_scan_record(
                                conn,
                                scan_id=session_id,
                                image_hash_sha256=image_hash_sha256,
                                language=req.user_preferences.language,
                                items=[
                                    {"dish_key": k, **items_by_key[k].model_dump()}
                                    for k in item_order
                                    if k in items_by_key
                                ],
                            )
                            await upsert_dish_knowledge_many(
                                conn,
                                rows=[
                                    {
                                        "dish_key": k,
                                        "translated_name": items_by_key[k].translated_name,
                                        "description": items_by_key[k].description,
                                        "tags": items_by_key[k].tags,
                                        "romanji": items_by_key[k].romanji,
                                    }
                                    for k in item_order
                                    if k in items_by_key and (items_by_key[k].translated_name or "").strip()
                                ],
                                language=req.user_preferences.language,
                                source_scan_id=session_id,
                            )

                    remaining_budget = max(0.0, ux_deadline - loop.time())
                    if remaining_budget > 0:
                        await asyncio.wait_for(
                            _db_write_scan_and_knowledge(),
                            timeout=min(db_timeout_s, remaining_budget),
                        )
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    logger.exception("DB write scan and knowledge failed")

                final_status = "completed"
            else:
                if not emitted_fatal_error:
                    code = "VLM_TIMEOUT" if isinstance(vlm_exc, asyncio.TimeoutError) else "VLM_FAILED"
                    detail = str(vlm_exc) if vlm_exc is not None else ""
                    yield sse_event(
                        "error",
                        {
                            "code": code,
                            "message": "解析逾時：請稍後重試或換一張更清晰的照片"
                            if code == "VLM_TIMEOUT"
                            else "解析失敗：請確認圖片清晰且為菜單",
                            "detail": detail,
                            "recoverable": True,
                        },
                    )
                    emitted_fatal_error = True
                final_status = "failed"

            top3: List[MenuItem] = []
            if items_by_key and loop.time() < ux_deadline:
                snapshot = _snapshot_items()
                top3_candidates = [i for i in snapshot if i.is_top3]
                changed_top3 = False

                if len(top3_candidates) > 3:
                    keep_ids = {i.id for i in top3_candidates[:3]}
                    for item in top3_candidates[3:]:
                        if item.is_top3:
                            item.is_top3 = False
                            if item.image_status != "none":
                                item.image_status = "none"
                            changed_top3 = True
                    top3_candidates = [i for i in snapshot if i.id in keep_ids]

                if not top3_candidates:
                    for item in snapshot[:3]:
                        if not item.is_top3:
                            item.is_top3 = True
                            changed_top3 = True
                        if item.image_status != "pending":
                            item.image_status = "pending"
                            changed_top3 = True
                        if not item.image_prompt.strip():
                            dish_name = (item.translated_name or item.original_name or "").strip()
                            item.image_prompt = (
                                "Japanese watercolor illustration, hand-drawn style, warm atmosphere, "
                                "studio ghibli food style, white background. Dish: "
                                f"{dish_name}."
                            ).strip()
                            changed_top3 = True
                    top3_candidates = [i for i in snapshot if i.is_top3]

                top3 = top3_candidates[:3]

                if changed_top3 and items_by_key:
                    now = loop.time()
                    if (not menu_data_emitted) or (now - last_menu_data_ts >= menu_data_min_interval_s):
                        yield sse_event(
                            "menu_data",
                            MenuDataEvent(session_id=session_id, items=_snapshot_items()).model_dump(),
                        )
                        menu_data_emitted = True
                        last_menu_data_ts = now

            if top3 and client is not None and loop.time() < ux_deadline:
                yield sse_event("status", _status_payload("generating_images", "主廚正在繪製招牌菜插畫..."))

                try:
                    async def _gen_one(item: MenuItem) -> tuple[MenuItem, bytes | None, Exception | None]:
                        try:
                            remaining_budget = max(0.0, ux_deadline - loop.time())
                            if remaining_budget <= 0:
                                raise asyncio.TimeoutError()
                            timeout_s = min(image_timeout_s, remaining_budget)
                            img = await asyncio.wait_for(
                                asyncio.to_thread(client.generate_food_image_bytes, prompt=item.image_prompt),
                                timeout=timeout_s,
                            )
                            return item, img, None
                        except Exception as e:
                            return item, None, e

                    tasks = {asyncio.create_task(_gen_one(item)): item for item in top3}
                    image_fallback_announced = False

                    for done_task in asyncio.as_completed(list(tasks.keys())):
                        if loop.time() >= ux_deadline:
                            break
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
                                    _status_payload(
                                        "generating_images",
                                        f"模型 {_PRIMARY_IMAGE_MODEL} 暫不可用，改用 {_FALLBACK_IMAGE_MODEL} 生圖...",
                                    ),
                                )
                                image_fallback_announced = True
                            try:
                                used_fallback = True
                                fallback_client = GeminiClient(
                                    api_key=google_api_key,
                                    vlm_model=getattr(client, "vlm_model", vlm_model),
                                    image_model=_FALLBACK_IMAGE_MODEL,
                                )
                                remaining_budget = max(0.0, ux_deadline - loop.time())
                                if remaining_budget <= 0:
                                    raise asyncio.TimeoutError()
                                timeout_s = min(image_timeout_s, remaining_budget)
                                fb = await asyncio.wait_for(
                                    asyncio.to_thread(
                                        fallback_client.generate_food_image_bytes,
                                        prompt=item.image_prompt,
                                    ),
                                    timeout=timeout_s,
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

                    if loop.time() >= ux_deadline:
                        for t, item in tasks.items():
                            if t.done():
                                continue
                            t.cancel()
                            await asyncio.gather(t, return_exceptions=True)
                            yield sse_event(
                                "image_update",
                                {
                                    "session_id": session_id,
                                    "item_id": item.id,
                                    "image_status": "failed",
                                    "image_url": "",
                                },
                            )
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
                    if not items_by_key:
                        final_status = "failed"
                    else:
                        final_status = "completed"

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("scan stream failed")
        if not emitted_fatal_error:
            yield sse_event(
                "error",
                {
                    "code": "INTERNAL_ERROR",
                    "message": "系統忙碌或發生未知錯誤，請稍後重試",
                    "detail": str(e),
                    "recoverable": True,
                },
            )
            emitted_fatal_error = True
        final_status = "failed"

    elapsed_ms = int(max(0.0, (loop.time() - started_at)) * 1000)
    snapshot = _snapshot_items()
    unknown_items_count = len([i for i in snapshot if not (i.translated_name or "").strip()])
    yield sse_event(
        "done",
        {
            "status": final_status,
            "session_id": session_id,
            "summary": {
                "elapsed_ms": elapsed_ms,
                "items_count": len(snapshot),
                "used_cache": used_cache,
                "used_fallback": used_fallback,
                "unknown_items_count": unknown_items_count,
            },
        },
    )

        


@app.post("/api/v1/scan/stream")
async def scan_stream(
    req: ScanRequest,
    accept: str | None = Header(default=None),
) -> StreamingResponse:
    if accept and "text/event-stream" not in accept:
        raise HTTPException(status_code=406, detail="Client must send Accept: text/event-stream")

    generator = _stream_scan(req)
    return StreamingResponse(generator, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
