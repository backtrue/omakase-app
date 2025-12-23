import ast
import json
import os
import re
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from .schemas import VlmDishStringsResponse, VlmMenuResponse

T = TypeVar("T", bound=BaseModel)


def _extract_first_balanced_json(text: str) -> Optional[str]:
    # Find the first balanced JSON object/array in the text.
    start_obj = text.find("{")
    start_arr = text.find("[")
    if start_obj == -1 and start_arr == -1:
        return None

    if start_obj == -1:
        start = start_arr
        open_ch, close_ch = "[", "]"
    elif start_arr == -1:
        start = start_obj
        open_ch, close_ch = "{", "}"
    else:
        if start_obj < start_arr:
            start = start_obj
            open_ch, close_ch = "{", "}"
        else:
            start = start_arr
            open_ch, close_ch = "[", "]"

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == open_ch:
            depth += 1
            continue
        if ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def _escape_newlines_in_json_strings(text: str) -> str:
    # Heuristic: models sometimes output raw newlines inside quoted strings.
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            if ch == "\n" or ch == "\r":
                out.append("\\n")
                continue
            out.append(ch)
            continue

        if ch == '"':
            out.append(ch)
            in_string = True
            continue

        out.append(ch)

    return "".join(out)


def _quote_unquoted_keys(text: str) -> str:
    return re.sub(
        r'([\{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*):',
        r'\1"\2"\3:',
        text,
    )


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([\]\}])", r"\1", text)


def _replace_python_literals(text: str) -> str:
    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    return text


def _convert_single_quoted_strings_to_double(text: str) -> str:
    out: list[str] = []
    in_dq = False
    in_sq = False
    escape = False

    for ch in text:
        if in_dq:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_dq = False
                continue
            out.append(ch)
            continue

        if in_sq:
            if escape:
                if ch == "'":
                    out.append("'")
                elif ch == "\\":
                    out.append("\\")
                else:
                    out.append("\\" + ch)
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == "'":
                out.append('"')
                in_sq = False
                continue
            if ch == '"':
                out.append('\\"')
                continue
            if ch == "\n" or ch == "\r":
                out.append("\\n")
                continue
            out.append(ch)
            continue

        if ch == '"':
            out.append(ch)
            in_dq = True
            continue

        if ch == "'":
            out.append('"')
            in_sq = True
            continue

        out.append(ch)

    return "".join(out)


def _repair_jsonish(text: str) -> str:
    text = _remove_trailing_commas(text)
    text = _replace_python_literals(text)
    text = _quote_unquoted_keys(text)
    text = _convert_single_quoted_strings_to_double(text)
    text = _remove_trailing_commas(text)
    return text


def _append_missing_closers(text: str) -> str:
    stack: list[str] = []
    in_string = False
    escape = False

    for ch in text:
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{" or ch == "[":
            stack.append(ch)
            continue

        if ch == "}" or ch == "]":
            if not stack:
                continue
            top = stack[-1]
            if (top == "{" and ch == "}") or (top == "[" and ch == "]"):
                stack.pop()
            continue

    if not stack:
        return text

    closers: list[str] = []
    for open_ch in reversed(stack):
        closers.append("}" if open_ch == "{" else "]")

    return text + "".join(closers)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _heuristic_extract_dish_strings(text: str) -> list[str]:
    raw = text.strip()
    if not raw:
        return []

    candidates: list[str] = []

    for m in re.finditer(r'"([^"\\]*(?:\\.[^"\\]*)*)"', raw):
        s = m.group(1)
        try:
            s = bytes(s, "utf-8").decode("unicode_escape")
        except Exception:
            pass
        s = s.strip()
        if s:
            candidates.append(s)

    for m in re.finditer(r"'([^'\\]*(?:\\.[^'\\]*)*)'", raw):
        s = m.group(1).strip()
        if s:
            candidates.append(s)

    if candidates:
        filtered = [s for s in candidates if len(s.strip()) >= 2]
        return _dedupe_preserve_order(filtered)

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    for ln in lines:
        ln = re.sub(r"^[-*]\s+", "", ln).strip()
        ln = re.sub(r"^\d+[\.)]\s+", "", ln).strip()
        if ln:
            candidates.append(ln)

    return _dedupe_preserve_order(candidates)


def _parse_json_fallback_schema(text: str, schema: Type[T]) -> T:
    # Some model responses may wrap JSON in markdown; keep v1 minimal.
    stripped = text.strip()

    # Remove common markdown code fences.
    if "```" in stripped:
        # Extract content inside the first fenced block if present.
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if m is not None:
            stripped = m.group(1).strip()
        else:
            stripped = stripped.replace("```", "").strip()

    # 1) Best case: it's valid JSON already.
    try:
        data = json.loads(stripped)
        return schema.model_validate(data)
    except Exception:
        pass

    # 2) Extract the first balanced JSON object/array from noisy text.
    candidate = _extract_first_balanced_json(stripped) or stripped

    try:
        data = json.loads(candidate)
        return schema.model_validate(data)
    except Exception:
        pass

    repaired = _escape_newlines_in_json_strings(candidate)

    try:
        data = json.loads(repaired)
        return schema.model_validate(data)
    except Exception:
        pass

    repaired2 = _repair_jsonish(repaired)
    repaired3 = _append_missing_closers(repaired2)
    try:
        data = json.loads(repaired3)
        return schema.model_validate(data)
    except Exception:
        pass

    try:
        data = ast.literal_eval(candidate)
        return schema.model_validate(data)
    except Exception:
        try:
            data = ast.literal_eval(repaired3)
            return schema.model_validate(data)
        except Exception:
            if schema is VlmDishStringsResponse:
                dish_strings = _heuristic_extract_dish_strings(candidate)
                return schema.model_validate({"dish_strings": dish_strings})
            raise


def _parse_json_fallback(text: str) -> VlmMenuResponse:
    return _parse_json_fallback_schema(text, VlmMenuResponse)


def _collect_text_fields(obj: Any, *, _visited: set[int], _depth: int) -> list[str]:
    if obj is None:
        return []
    if _depth <= 0:
        return []

    oid = id(obj)
    if oid in _visited:
        return []
    _visited.add(oid)

    out: list[str] = []

    if isinstance(obj, str):
        return []

    if isinstance(obj, dict):
        t = obj.get("text")
        if isinstance(t, str) and t.strip():
            out.append(t)
        for k in ("candidates", "candidate", "content", "parts"):
            v = obj.get(k)
            if v is not None:
                out.extend(_collect_text_fields(v, _visited=_visited, _depth=_depth - 1))
        return out

    if isinstance(obj, (list, tuple)):
        for it in obj:
            out.extend(_collect_text_fields(it, _visited=_visited, _depth=_depth - 1))
        return out

    t = getattr(obj, "text", None)
    if isinstance(t, str) and t.strip():
        out.append(t)

    for attr in ("candidates", "candidate", "content", "parts"):
        try:
            v = getattr(obj, attr, None)
        except Exception:
            v = None
        if v is not None:
            out.extend(_collect_text_fields(v, _visited=_visited, _depth=_depth - 1))

    return out


def _extract_text_from_response(response: object) -> Optional[str]:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text

    parts = _collect_text_fields(response, _visited=set(), _depth=7)
    joined = "".join([p for p in parts if isinstance(p, str)]).strip()
    if joined:
        return joined

    if isinstance(text, str) and text:
        return text

    return None


def _empty_response_error(response: object) -> RuntimeError:
    finish_reason = None
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
    except Exception:
        finish_reason = None

    prompt_feedback = getattr(response, "prompt_feedback", None)
    return RuntimeError(
        "Gemini VLM returned no parsed JSON and no text"
        + (f" (finish_reason={finish_reason})" if finish_reason is not None else "")
        + (f" (prompt_feedback={prompt_feedback!r})" if prompt_feedback is not None else "")
    )


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        vlm_model: str,
        image_model: str,
    ) -> None:
        from google import genai

        self._genai = genai
        self._api_key = api_key
        self.vlm_model = vlm_model
        self.image_model = image_model
        try:
            timeout_s = float(os.getenv("GENAI_HTTP_TIMEOUT_SECONDS", "300"))
        except Exception:
            timeout_s = 300.0
        timeout_ms = max(1000, int(timeout_s * 1000))
        try:
            self._client = genai.Client(api_key=api_key, http_options={"timeout": timeout_ms})
        except TypeError:
            self._client = genai.Client(api_key=api_key)

    def parse_menu_from_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> VlmMenuResponse:
        from google.genai import types

        max_output_tokens = int(os.getenv("VLM_MAX_OUTPUT_TOKENS", "8192"))
        temperature = float(os.getenv("VLM_TEMPERATURE", "0.2"))

        response = self._client.models.generate_content(
            model=self.vlm_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VlmMenuResponse,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        if getattr(response, "parsed", None) is not None:
            return response.parsed

        # Fallback for cases where parsing is not returned.
        text = _extract_text_from_response(response)
        if text is not None:
            return _parse_json_fallback(text)

        raise _empty_response_error(response)

    def parse_dish_strings_from_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> VlmDishStringsResponse:
        from google.genai import types

        max_output_tokens = int(os.getenv("OCR_MAX_OUTPUT_TOKENS", "4096"))
        temperature = float(os.getenv("OCR_TEMPERATURE", "0.0"))

        response = self._client.models.generate_content(
            model=self.vlm_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VlmDishStringsResponse,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        if getattr(response, "parsed", None) is not None:
            return response.parsed

        text = _extract_text_from_response(response)
        if text is not None:
            return _parse_json_fallback_schema(text, VlmDishStringsResponse)

        raise _empty_response_error(response)

    async def parse_dish_strings_from_image_async(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> VlmDishStringsResponse:
        from google.genai import types

        max_output_tokens = int(os.getenv("OCR_MAX_OUTPUT_TOKENS", "4096"))
        temperature = float(os.getenv("OCR_TEMPERATURE", "0.0"))

        response = await self._client.aio.models.generate_content(
            model=self.vlm_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VlmDishStringsResponse,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        if getattr(response, "parsed", None) is not None:
            return response.parsed

        text = _extract_text_from_response(response)
        if text is not None:
            return _parse_json_fallback_schema(text, VlmDishStringsResponse)

        raise _empty_response_error(response)

    def translate_menu_items(
        self,
        *,
        prompt: str,
    ) -> VlmMenuResponse:
        from google.genai import types

        max_output_tokens = int(os.getenv("TRANSLATE_MAX_OUTPUT_TOKENS", os.getenv("VLM_MAX_OUTPUT_TOKENS", "8192")))
        temperature = float(os.getenv("TRANSLATE_TEMPERATURE", os.getenv("VLM_TEMPERATURE", "0.0")))

        response = self._client.models.generate_content(
            model=self.vlm_model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VlmMenuResponse,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        if getattr(response, "parsed", None) is not None:
            return response.parsed

        text = _extract_text_from_response(response)
        if text is not None:
            return _parse_json_fallback(text)

        raise _empty_response_error(response)

    async def translate_menu_items_async(
        self,
        *,
        prompt: str,
    ) -> VlmMenuResponse:
        from google.genai import types

        max_output_tokens = int(os.getenv("TRANSLATE_MAX_OUTPUT_TOKENS", os.getenv("VLM_MAX_OUTPUT_TOKENS", "8192")))
        temperature = float(os.getenv("TRANSLATE_TEMPERATURE", os.getenv("VLM_TEMPERATURE", "0.0")))

        response = await self._client.aio.models.generate_content(
            model=self.vlm_model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VlmMenuResponse,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        if getattr(response, "parsed", None) is not None:
            return response.parsed

        text = _extract_text_from_response(response)
        if text is not None:
            return _parse_json_fallback(text)

        raise _empty_response_error(response)

    async def parse_menu_from_image_async(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> VlmMenuResponse:
        from google.genai import types

        max_output_tokens = int(os.getenv("VLM_MAX_OUTPUT_TOKENS", "8192"))
        temperature = float(os.getenv("VLM_TEMPERATURE", "0.2"))

        response = await self._client.aio.models.generate_content(
            model=self.vlm_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VlmMenuResponse,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )

        if getattr(response, "parsed", None) is not None:
            return response.parsed

        text = _extract_text_from_response(response)
        if text is not None:
            return _parse_json_fallback(text)

        raise _empty_response_error(response)

    def generate_food_image_bytes(
        self,
        *,
        prompt: str,
        aspect_ratio: str = "1:1",
    ) -> bytes:
        import logging
        from google.genai import types

        logger = logging.getLogger(__name__)
        logger.info(f"Generating image with model={self.image_model}, prompt_len={len(prompt)}")

        # Prefer Imagen for text-to-image.
        if self.image_model.startswith("imagen-"):
            logger.info("Using Imagen API (generate_images)")
            result = self._client.models.generate_images(
                model=self.image_model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                    safety_filter_level="BLOCK_LOW_AND_ABOVE",
                    person_generation="ALLOW_ADULT",
                ),
            )
            if not result.generated_images:
                logger.error("Imagen returned no images")
                raise RuntimeError("Imagen returned no images")
            logger.info("Imagen generation successful")
            return result.generated_images[0].image.image_bytes

        # Fallback: Gemini native image generation model.
        logger.info("Using Gemini native image generation (generate_content)")
        response = self._client.models.generate_content(
            model=self.image_model,
            contents=[prompt],
        )

        parts = getattr(response, "parts", []) or []
        logger.info(f"Response received, parts count: {len(parts)}")
        
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None) is not None:
                data = inline.data
                # Depending on SDK version, this may be bytes or base64 string.
                if isinstance(data, str):
                    import base64
                    logger.info("Image data received as base64 string")
                    return base64.b64decode(data)
                logger.info("Image data received as bytes")
                return data

        logger.error(f"Image model returned no inline image data. Parts: {len(parts)}, Response: {response}")
        raise RuntimeError("Image model returned no inline image data")
